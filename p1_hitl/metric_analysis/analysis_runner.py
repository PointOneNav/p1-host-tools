import io
import logging
import time
import traceback
from pathlib import Path
from typing import Iterator, List, Optional

from fusion_engine_client.messages import (MessageHeader, VersionInfoMessage,
                                           message_type_to_class)
from fusion_engine_client.parsers import fast_indexer

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric,
                                             FatalMetricException,
                                             MaxElapsedTimeMetric,
                                             MetricController, TimeSource,
                                             Timestamp)
from p1_runner.device_interface import (MAX_FE_MSG_SIZE, DeviceInterface,
                                        FusionEngineDecoder,
                                        MessageWithBytesTuple)
from p1_runner.exception_utils import exception_to_str

from .base_analysis import AnalyzerBase
from .position_analysis import PositionAnalyzer
from .sanity_analysis import SanityAnalyzer

logger = logging.getLogger('point_one.hitl.analysis')

MAX_SEC_TO_VERSION_MESSAGE = 60

metric_message_host_time_elapsed = MaxElapsedTimeMetric(
    'message_host_time_elapsed',
    'Max time to first message, and between subsequent messages.',
    TimeSource.HOST,
    max_time_to_first_check_sec=10,
    max_time_between_checks_sec=0.2,
    not_logged=True
)
metric_message_host_time_elapsed_test_stop = MaxElapsedTimeMetric(
    'message_host_time_elapsed_test_stop',
    'If no messages are received for this duration (before or after first message), stop the test.',
    TimeSource.HOST,
    max_time_to_first_check_sec=60,
    max_time_between_checks_sec=60,
    is_fatal=True,
    not_logged=True
)
metric_version_check = AlwaysTrueMetric(
    'version_check',
    'Check that the version message matches the expected value.',
    is_fatal=True
)

# TODO: Figure out way to measure message latency

CONSOLE_UPDATE_INTERVAL_SEC = 30
PLAYBACK_READ_SIZE = 1024
REALTIME_POLL_INTERVAL = 0.05


def _setup_analysis(env_args: HitlEnvArgs) -> List[AnalyzerBase]:
    MetricController.apply_environment_config_customizations(env_args)

    analyzers = [SanityAnalyzer(), PositionAnalyzer()]
    for analyzer in analyzers:
        analyzer.configure(env_args)
    return analyzers


def custom_json(obj):
    if isinstance(obj, Timestamp):
        return {'host_time': obj.host_time, 'p1_time': obj.p1_time, 'system_time': obj.system_time}
    else:
        return str(obj)


def _finish_analysis() -> bool:
    MetricController.finalize()
    report = MetricController.generate_report()
    return report['has_failures']


def run_analysis(interface: DeviceInterface, env_args: HitlEnvArgs,
                 output_dir: Path, log_metric_values: bool, release_str: str) -> Optional[bool]:
    try:
        params = env_args.get_selected_test_type().get_test_params()
        MetricController.enable_logging(output_dir, True, log_metric_values)
        if params.duration_sec < MAX_SEC_TO_VERSION_MESSAGE:
            metric_version_check.is_disabled = True

        analyzers = _setup_analysis(env_args)
        start_time = time.monotonic()
        logger.info(f'Monitoring device for {params.duration_sec} sec.')
        msg_count = 0
        last_logger_update = time.monotonic()
        while time.monotonic() - start_time < params.duration_sec:
            try:
                msgs = interface.poll_messages(response_timeout=REALTIME_POLL_INTERVAL)
            except Exception as e:
                logger.error(f'Exception collecting FusionEngine messages from device {exception_to_str(e)}')
                return None
            MetricController.update_host_time()

            for msg in msgs:
                msg_count += 1
                MetricController.update_device_time(msg)
                metric_message_host_time_elapsed.check()
                metric_message_host_time_elapsed_test_stop.check()
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
        return None

    return _finish_analysis()


def run_analysis_playback(playback_path: Path, env_args: HitlEnvArgs,
                          output_dir: Path, log_metric_values: bool) -> Optional[bool]:
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
        MetricController.enable_logging(output_dir, False, log_metric_values)
        MetricController.playback_host_time(output_dir.parent)
        analyzers = _setup_analysis(env_args)

        metric_message_host_time_elapsed.is_disabled = True
        metric_message_host_time_elapsed_test_stop.is_disabled = True
        metric_version_check.is_disabled = True

        for msg in _fast_decoder(playback_path):
            MetricController.update_device_time(msg)
            for analyzer in analyzers:
                analyzer.update(msg)
                pass

    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while analyzing FE messages:\n{traceback.format_exc()}')
        return None

    return _finish_analysis()
