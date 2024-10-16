import io
import logging
import time
import traceback
from pathlib import Path
from typing import List

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (FatalMetricException,
                                             MaxElapsedTimeMetric,
                                             MetricController, TimeSource)
from p1_runner.device_interface import (MAX_FE_MSG_SIZE, DeviceInterface,
                                        FusionEngineDecoder,
                                        MessageWithBytesTuple)
from p1_runner.exception_utils import exception_to_str

from .base_analysis import AnalyzerBase
from .position_analysis import PositionAnalyzer
from .sanity_analysis import SanityAnalyzer

logger = logging.getLogger('point_one.hitl.analysis')

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

# TODO: Figure out way to measure message latency

LOGGER_UPDATE_INTERVAL_SEC = 30
PLAYBACK_READ_SIZE = 1024


def _setup_analysis(env_args: HitlEnvArgs, output_dir: Path, log_metric_values: bool) -> List[AnalyzerBase]:
    MetricController.enable_logging(output_dir, True, log_metric_values)
    MetricController.apply_environment_config_customizations(env_args)

    analyzers = [SanityAnalyzer(), PositionAnalyzer()]
    for analyzer in analyzers:
        analyzer.configure(env_args)
    return analyzers


def _finish_analysis():
    MetricController.finalize()
    report = MetricController.generate_report()
    results = report['results']

    for k, v in results.items():
        if v['failure_time'] is None:
            if not v['was_checked']:
                logger.info(f'[MISS]: {k}')
            else:
                logger.info(f'[GOOD]: {k}')
        else:
            logger.warning(f'[FAIL]: {k}')
            logger.info(v)


def run_analysis(interface: DeviceInterface, env_args: HitlEnvArgs, output_dir: Path, log_metric_values: bool) -> bool:
    try:
        params = env_args.HITL_TEST_TYPE.get_test_params()
        analyzers = _setup_analysis(env_args, output_dir, log_metric_values)
        start_time = time.monotonic()
        logger.info(f'Monitoring device for {params.duration_sec} sec.')
        msg_count = 0
        last_logger_update = time.monotonic()
        while time.monotonic() - start_time < params.duration_sec:
            try:
                msgs = interface.wait_for_any_fe_message(response_timeout=0.1)
            except Exception as e:
                logger.error(f'Exception collecting FusionEngine messages from device {exception_to_str(e)}')
                return False
            MetricController.update_host_time()
            now = time.monotonic()
            if now - last_logger_update > LOGGER_UPDATE_INTERVAL_SEC:
                elapsed = now - start_time
                logger.info(f'{round(elapsed)}/{params.duration_sec} elapsed. {msg_count} messages from device.')
                last_logger_update = now

            for msg in msgs:
                msg_count += 1
                MetricController.update_device_time(msg)
                metric_message_host_time_elapsed.check()
                metric_message_host_time_elapsed_test_stop.check()
                for analyzer in analyzers:
                    analyzer.update(msg)

    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while analyzing FE messages:\n{traceback.format_exc()}')
        return False

    _finish_analysis()

    return True


def run_analysis_playback(playback_path: Path, env_args: HitlEnvArgs,
                          output_dir: Path, log_metric_values: bool) -> bool:
    try:
        analyzers = _setup_analysis(env_args, output_dir, log_metric_values)

        metric_message_host_time_elapsed.is_disabled = True
        metric_message_host_time_elapsed_test_stop.is_disabled = True

        fe_decoder = FusionEngineDecoder(MAX_FE_MSG_SIZE, warn_on_unrecognized=False, return_bytes=True)

        with open(playback_path, 'rb') as in_fd:
            in_fd.seek(0, io.SEEK_END)
            file_size = in_fd.tell()
            in_fd.seek(0, io.SEEK_SET)

            start_time = time.monotonic()
            logger.info(f'Playing back {playback_path} ({file_size/1024/1024} MB).')
            msg_count = 0
            last_logger_update = time.monotonic()

            for chunk in iter(lambda: in_fd.read(PLAYBACK_READ_SIZE), b''):
                msgs: List[MessageWithBytesTuple] = fe_decoder.on_data(chunk)  # type: ignore
                now = time.monotonic()
                if now - last_logger_update > LOGGER_UPDATE_INTERVAL_SEC:
                    elapsed_sec = now - start_time
                    total_bytes_read = in_fd.tell()
                    logger.log(logging.INFO,
                               'Processed %d/%d bytes (%.1f%%). msg_count: %d. [elapsed=%.1f sec, rate=%.1f MB/s]' %
                               (total_bytes_read, file_size, 100.0 * float(total_bytes_read) / file_size, msg_count,
                                elapsed_sec, total_bytes_read / elapsed_sec / 1e6))
                    last_logger_update = now

                for msg in msgs:
                    msg_count += 1
                    MetricController.update_device_time(msg)
                    for analyzer in analyzers:
                        analyzer.update(msg)

    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while analyzing FE messages:\n{traceback.format_exc()}')
        return False

    _finish_analysis()

    return True
