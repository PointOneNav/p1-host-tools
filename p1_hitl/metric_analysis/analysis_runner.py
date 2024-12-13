import io
import logging
import time
import traceback
from pathlib import Path
from typing import Iterator, List

from fusion_engine_client.messages import (MessageHeader, VersionInfoMessage,
                                           message_type_to_class)
from fusion_engine_client.parsers import fast_indexer

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.device_interfaces.scenario_controller import ScenarioController
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric,
                                             FatalMetricException,
                                             MaxTimeBetweenChecks,
                                             MaxTimeToFirstCheckMetric,
                                             MaxValueMetric, MetricController,
                                             TimeSource)
from p1_runner.device_interface import (MAX_FE_MSG_SIZE, DeviceInterface,
                                        FusionEngineDecoder,
                                        MessageWithBytesTuple)
from p1_runner.exception_utils import exception_to_str

from .base_analysis import AnalyzerBase
from .position_analysis import PositionAnalyzer
from .reset_ttff_analysis import ResetTTFFAnalyzer
from .sanity_analysis import SanityAnalyzer

logger = logging.getLogger('point_one.hitl.analysis')

MAX_SEC_TO_VERSION_MESSAGE = 60

metric_host_time_to_first_message = MaxTimeToFirstCheckMetric(
    'host_time_to_first_message',
    '''Max host time to first FE message.
A failure indicates FE messages aren't being output from the device.
It is also possible that the test setup had an issue (disconnected cable, bad network, etc.).''',
    TimeSource.HOST,
    max_time_to_first_check_sec=10,
    is_fatal=True,
)
metric_host_time_between_messages = MaxTimeBetweenChecks(
    'host_time_between_messages',
    '''Max time between FE messages.
A failure indicates FE messages aren't getting from the device under test to the tester at the rate expected.
This may be because the DUT stopped outputting messages, or had a large delay in its output.
It is also possible that the testing host was trying to service too many devices and fell behind.''',
    TimeSource.HOST,
    max_time_between_checks_sec=0.2,
)
metric_host_time_between_messages_stop = MaxTimeBetweenChecks(
    'host_time_between_messages_stop',
    '''If no messages are received for this duration, stop the test.
See host_time_between_messages for more details.''',
    TimeSource.HOST,
    max_time_between_checks_sec=10,
    is_fatal=True,
)
metric_version_check = AlwaysTrueMetric(
    'version_check',
    'Check that the version message sent from the device matches the expected value.',
    is_fatal=True
)
# This check is disabled for builds like Quectel that mix in non-FE data.
metric_no_fe_data_gaps = MaxValueMetric(
    'no_fe_data_gaps',
    '''Checks the number of bytes not wrapped in a valid FE message.
This will trigger on messages of different protocols, malformed messages, or dropped data.''',
    0,
    is_fatal=True
)


def configure_metrics(env_args: HitlEnvArgs):
    # Expect gaps in FE data from LG69T
    if env_args.HITL_BUILD_TYPE.is_lg69t():
        metric_no_fe_data_gaps.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)

# TODO: Figure out way to measure message latency

CONSOLE_UPDATE_INTERVAL_SEC = 30
PLAYBACK_READ_SIZE = 1024
REALTIME_POLL_INTERVAL = 0.05


def _setup_analysis(env_args: HitlEnvArgs) -> List[AnalyzerBase]:
    analyzers = [c(env_args) for c in [SanityAnalyzer, PositionAnalyzer, ResetTTFFAnalyzer]]
    return analyzers


def run_analysis(interface: DeviceInterface, env_args: HitlEnvArgs, log_dir: Path,
                 release_str: str) -> bool:
    try:
        params = env_args.get_selected_test_type().get_test_params()
        if params.duration_sec < MAX_SEC_TO_VERSION_MESSAGE:
            metric_version_check.is_disabled = True

        analyzers = _setup_analysis(env_args)
        scenario_controller = ScenarioController(env_args, log_dir, device_interface=interface)
        start_time = time.monotonic()
        logger.info(f'Monitoring device for {params.duration_sec} sec.')
        msg_count = 0
        last_logger_update = time.monotonic()
        # Used to look for CRC errors or gaps in FE data.
        interface.fe_decoder._return_offset = True
        last_message_end_offset = 0
        while time.monotonic() - start_time < params.duration_sec:
            try:
                msgs = interface.poll_messages(response_timeout=REALTIME_POLL_INTERVAL)
            except Exception as e:
                logger.error(f'Exception collecting FusionEngine messages from device {exception_to_str(e)}')
                return False
            MetricController.update_host_time()

            events = scenario_controller.update_controller()
            for event in events:
                for analyzer in analyzers:
                    analyzer.on_event(event)

            for msg in msgs:
                msg_count += 1
                # The type hint is wrong since it ignores _return_offset.
                msg_offset: int = msg[3]  # type: ignore
                msg = msg[:3]
                MetricController.update_device_time(msg)
                metric_host_time_to_first_message.check()
                metric_host_time_between_messages.check()
                metric_host_time_between_messages_stop.check()

                # Check for gaps in data
                metric_no_fe_data_gaps.check(msg_offset - last_message_end_offset)
                last_message_end_offset = len(msg[2]) + msg_offset

                payload = msg[1]
                if isinstance(payload, VersionInfoMessage):
                    context = f'Received: {payload.engine_version_str} != Expected: {release_str}'
                    metric_version_check.check(payload.engine_version_str == release_str, context)
                for analyzer in analyzers:
                    analyzer.update(msg)

            now = time.monotonic()
            if now - last_logger_update > CONSOLE_UPDATE_INTERVAL_SEC:
                elapsed = now - start_time
                logger.info(f'{round(elapsed)}/{params.duration_sec} elapsed. {msg_count} messages from device.')
                last_logger_update = now

    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while analyzing FE messages:\n{traceback.format_exc()}')
        return False

    return True


def run_analysis_playback(playback_path: Path, env_args: HitlEnvArgs) -> bool:
    class _PlaybackStatus:
        def __init__(self, in_fd) -> None:
            self.in_fd = in_fd
            self.in_fd.seek(0, io.SEEK_END)
            self.file_size = in_fd.tell()
            self.in_fd.seek(0, io.SEEK_SET)
            self.start_time = time.monotonic()
            logger.info(f'Playing back {playback_path} ({self.file_size/1024/1024} MB).')
            self.msg_count = 0
            self.last_logger_update = time.monotonic()

        def update(self):
            now = time.monotonic()
            if now - self.last_logger_update > CONSOLE_UPDATE_INTERVAL_SEC:
                elapsed_sec = now - self.start_time
                total_bytes_read = self.in_fd.tell()
                logger.log(
                    logging.INFO,
                    'Processed %d/%d bytes (%.1f%%). msg_count: %d. [elapsed=%.1f sec, rate=%.1f MB/s]' %
                    (total_bytes_read,
                     self.file_size,
                     100.0 * float(total_bytes_read) / self.file_size,
                     self.msg_count,
                     elapsed_sec,
                     total_bytes_read / elapsed_sec / 1e6))
                self.last_logger_update = now

    # Can be used to replicate specific decoder behaviors, but is much slower.
    def _slow_decoder(playback_path: Path) -> Iterator[MessageWithBytesTuple]:
        fe_decoder = FusionEngineDecoder(MAX_FE_MSG_SIZE, warn_on_unrecognized=False, return_bytes=True)
        with open(playback_path, 'rb') as in_fd:
            status = _PlaybackStatus(in_fd)
            for chunk in iter(lambda: in_fd.read(PLAYBACK_READ_SIZE), b''):
                msgs: List[MessageWithBytesTuple] = fe_decoder.on_data(chunk)  # type: ignore
                for msg in msgs:
                    status.msg_count += 1
                    yield msg
                status.update()

    # This decoder is fast enough that it no longer dominates the execution time. The
    # MetricController.update_device_time(msg) is currently the hot path for optimization.
    def _fast_decoder(playback_path: Path) -> Iterator[MessageWithBytesTuple]:
        file_index = fast_indexer.fast_generate_index(str(playback_path))
        HEADER_SIZE = MessageHeader.calcsize()
        with open(playback_path, 'rb') as in_fd:
            status = _PlaybackStatus(in_fd)
            header = MessageHeader()
            for offset in file_index.offset:
                in_fd.seek(offset)
                data = in_fd.read(HEADER_SIZE)
                header.unpack(data)
                data = in_fd.read(header.payload_size_bytes)
                if header.message_type in message_type_to_class:
                    message = message_type_to_class[header.message_type]()
                    message.unpack(data)
                else:
                    message = data
                yield header, message, data
                status.msg_count += 1
                status.update()

    try:
        analyzers = _setup_analysis(env_args)
        log_dir = playback_path.parent
        scenario_controller = ScenarioController(env_args, log_dir)

        # Don't check the metrics from this file. These are primarily data integrity checks.
        for metric in MetricController.get_metrics_in_this_file():
            metric.is_disabled = True

        for msg in _fast_decoder(playback_path):
            MetricController.update_device_time(msg)

            events = scenario_controller.update_controller()
            for event in events:
                for analyzer in analyzers:
                    analyzer.on_event(event)

            for analyzer in analyzers:
                analyzer.update(msg)
                pass

    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while analyzing FE messages:\n{traceback.format_exc()}')
        return False

    return True
