

import struct
from typing import Optional

from fusion_engine_client.messages import (EventNotificationMessage,
                                           MessagePayload, PoseMessage,
                                           Timestamp)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.metric_analysis.metrics import (EqualValueMetric,
                                             MaxElapsedTimeMetric,
                                             MinValueMetric, TimeSource)

from .base_analysis import AnalyzerBase

metric_seq_num_gap = EqualValueMetric(
    'seq_num_check',
    'Each FE sequence number should go up by one.',
    1,
    not_logged=True
)

metric_error_msg_count = EqualValueMetric(
    'error_msg_count',
    'Number of error notification messages received.',
    0,
    not_logged=True
)

class SanityAnalyzer(AnalyzerBase):
    def __init__(self) -> None:
        self.last_seq_num: Optional[int] = None
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
