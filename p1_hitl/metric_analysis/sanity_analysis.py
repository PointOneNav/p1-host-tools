

import struct
from typing import Optional

from fusion_engine_client.messages import (DataType, EventNotificationMessage,
                                           MessagePayload,
                                           PlatformStorageDataMessage,
                                           ProfileSystemStatusMessage,
                                           Timestamp)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.metric_analysis.metrics import (CdfThreshold, EqualValueMetric,
                                             MaxElapsedTimeMetric,
                                             MaxValueMetric, MinValueMetric,
                                             StatsMetric, TimeSource)

from .base_analysis import AnalyzerBase

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
    'Check P1Time goes forward monotonically.',
    0,

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
    max_cdf_thresholds=[CdfThreshold(50, 50)]
)

metric_mem_usage = MaxValueMetric(
    'mem_usage',
    'Checks that total memory usage is acceptable.',
    # Makes sure memory usage is below 15MB.
    15 * 1024 * 1024
)


class SanityAnalyzer(AnalyzerBase):
    def __init__(self) -> None:
        self.last_seq_num: Optional[int] = None
        self.last_p1_time: Optional[Timestamp] = None
        self.error_count = 0

    def update(self, msg: MessageWithBytesTuple):
        header, payload, _ = msg

        if self.last_seq_num is not None:
            metric_seq_num_gap.check(header.sequence_number - self.last_seq_num)
        self.last_seq_num = header.sequence_number

        if isinstance(payload, MessagePayload):
            if isinstance(payload, EventNotificationMessage):
                # Convert the unsigned event_flags to a signed value.
                signed_flag = struct.unpack('q', struct.pack('Q', payload.event_flags))[0]
                if signed_flag < 0:
                    self.error_count += 1

            metric_error_msg_count.check(self.error_count)

            if isinstance(payload, PlatformStorageDataMessage):
                if payload.data_type == DataType.CALIBRATION_STATE:
                    metric_calibration_received.check()
                elif payload.data_type == DataType.FILTER_STATE:
                    metric_filter_state_received.check()
                elif payload.data_type == DataType.USER_CONFIG:
                    metric_user_config_received.check()

            if isinstance(payload, ProfileSystemStatusMessage):
                metric_cpu_usage.check(payload.total_cpu_usage)
                metric_mem_usage.check(payload.used_memory_bytes)
            # We want to ignore p1_time from ProfileSystemStatusMessage since it just uses the last measurement it
            # received which may be in the past.
            else:
                p1_time = payload.get_p1_time()
                if p1_time:
                    if self.last_p1_time is not None:
                        metric_monotonic_p1time.check(p1_time.seconds - self.last_p1_time.seconds)
                    self.last_p1_time = p1_time
