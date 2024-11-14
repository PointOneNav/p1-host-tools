

import logging
import struct
from typing import Optional

from fusion_engine_client.messages import (DataType, EventNotificationMessage,
                                           InternalMessageType, MessagePayload,
                                           PlatformStorageDataMessage,
                                           ProfileDefinitionMessage,
                                           ProfileFreeRtosSystemStatusMessage,
                                           ProfileSystemStatusMessage,
                                           Timestamp)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (CdfThreshold, EqualValueMetric,
                                             MaxElapsedTimeMetric,
                                             MaxValueMetric, MetricController,
                                             MinValueMetric, StatsMetric,
                                             TimeSource)

from .base_analysis import AnalyzerBase

logger = logging.getLogger('point_one.hitl.analysis.sanity')

metric_seq_num_gap = EqualValueMetric(
    'seq_num_check',
    'Each FE sequence number should go up by one.',
    1,
)

metric_error_msg_count = EqualValueMetric(
    'error_msg_count',
    'Number of error notification messages received.',
    0,
)

metric_monotonic_p1time = MinValueMetric(
    'monotonic_p1time',
    'Check P1Time goes forward (mostly) monotonically.',
    -0.1,
)

metric_user_config_received = MaxElapsedTimeMetric(
    'user_config_received',
    'Checks that UserConfig storage is sent out periodically.',
    time_source=TimeSource.P1,
    max_time_to_first_check_sec=60,
)

metric_filter_state_received = MaxElapsedTimeMetric(
    'filter_state_received',
    'Checks that FilterState storage is sent out periodically.',
    time_source=TimeSource.P1,
    max_time_to_first_check_sec=60,
)

metric_calibration_received = MaxElapsedTimeMetric(
    'calibration_received',
    'Checks that Calibration storage is sent out periodically.',
    time_source=TimeSource.P1,
    max_time_to_first_check_sec=60,
)

metric_cpu_usage = StatsMetric(
    'cpu_usage',
    'Checks that total CPU usage is acceptable.',
    max_threshold=75,
    # Check median total CPU usage is below 50%.
    max_cdf_thresholds=[CdfThreshold(50, 50)],
    is_required=True
)

metric_mem_usage = MaxValueMetric(
    'mem_usage',
    'Checks that total memory usage is acceptable.',
    # Makes sure memory usage is below 15MB.
    15 * 1024 * 1024,
    is_required=True
)


def configure_metrics(env_args: HitlEnvArgs):
    # LG69T_AM does not output FilterState or Calibration.
    if env_args.HITL_BUILD_TYPE == DeviceType.LG69T_AM:
        metric_filter_state_received.is_disabled = True
        metric_calibration_received.is_disabled = True

    # Set processor resource usage for LG69T.
    if env_args.HITL_BUILD_TYPE.is_lg69t():
        metric_cpu_usage.max_threshold = 90
        metric_cpu_usage.max_cdf_thresholds = [CdfThreshold(50, 80)]
        # Assumes total RAM is 64KB, so remaining should be > 9KB.
        metric_mem_usage.threshold = 55 * 1024


MetricController.register_environment_config_customizations(configure_metrics)

_RTOS_IDLE_TASK_NAME = 'IDLE'


class SanityAnalyzer(AnalyzerBase):
    def __init__(self) -> None:
        self.last_seq_num: Optional[int] = None
        self.last_p1_time: Optional[Timestamp] = None
        self.error_count = 0
        self.rtos_task_name_map: dict[str, int] = {}
        self.env_args = None

    def configure(self, env_args: HitlEnvArgs):
        self.env_args = env_args

    def update(self, msg: MessageWithBytesTuple):
        assert isinstance(self.env_args, HitlEnvArgs)
        header, payload, _ = msg

        if self.last_seq_num is not None:
            metric_seq_num_gap.check(header.sequence_number - self.last_seq_num)
        self.last_seq_num = header.sequence_number

        if isinstance(payload, MessagePayload):
            # We want to ignore p1_time from ProfileSystemStatusMessage since it just uses the last measurement it
            # received which may be in the past.
            if not isinstance(payload, ProfileSystemStatusMessage):
                p1_time = payload.get_p1_time()
                if p1_time:
                    if self.last_p1_time is not None:
                        metric_monotonic_p1time.check(p1_time.seconds - self.last_p1_time.seconds)
                    self.last_p1_time = p1_time

            if isinstance(payload, EventNotificationMessage):
                # Convert the unsigned event_flags to a signed value.
                signed_flag = struct.unpack('q', struct.pack('Q', payload.event_flags))[0]
                if signed_flag < 0:
                    self.error_count += 1
                    logger.info(f'Error EventNotification: {payload}')
            elif isinstance(payload, PlatformStorageDataMessage):
                if payload.data_type == DataType.CALIBRATION_STATE:
                    metric_calibration_received.check()
                elif payload.data_type == DataType.FILTER_STATE:
                    metric_filter_state_received.check()
                elif payload.data_type == DataType.USER_CONFIG:
                    metric_user_config_received.check()
            elif header.message_type == InternalMessageType.PROFILE_FREERTOS_TASK_DEFINITION and \
                    isinstance(payload, ProfileDefinitionMessage):
                self.rtos_task_name_map = {v: k for k, v in payload.to_dict().items()}
            elif isinstance(payload, ProfileFreeRtosSystemStatusMessage):
                # Skip updates with no usage that follow a reset.
                if any(entry.cpu_usage != 0 for entry in payload.task_entries):
                    # Can only check CPU usage after getting task definitions.
                    if len(self.rtos_task_name_map) > 0:
                        idle_task_idx = self.rtos_task_name_map[_RTOS_IDLE_TASK_NAME]
                        metric_cpu_usage.check(100.0 - payload.task_entries[idle_task_idx].cpu_usage)
                    if self.env_args.HITL_BUILD_TYPE.is_lg69t:
                        total_memory = 64 * 1024
                    else:
                        raise NotImplementedError(f'Total memory not known for {self.env_args.HITL_BUILD_TYPE}.')
                    # Type check thinks these are constants.
                    metric_mem_usage.check(total_memory - payload.sbrk_free_bytes)  # type: ignore
                    metric_mem_usage.check(total_memory - payload.heap_free_bytes)  # type: ignore
            elif isinstance(payload, ProfileSystemStatusMessage):
                metric_cpu_usage.check(payload.total_cpu_usage)
                metric_mem_usage.check(payload.used_memory_bytes)

        # Check the error count here so it doesn't report this metric as skipped if no notifications occur.
        metric_error_msg_count.check(self.error_count)
