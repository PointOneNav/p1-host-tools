import struct

from fusion_engine_client.messages import EventNotificationMessage, EventType

LOG_LEVEL_FLAGS = {
    -3: 'FATAL',
    -2: 'ERROR',
    -1: 'WARNING',
    0: 'INFO'
}


def is_error_log_event(event: EventNotificationMessage) -> bool:
    return event.event_type == EventType.LOG and get_signed_event_flag(event) < 0


def get_signed_event_flag(event: EventNotificationMessage) -> int:
    # Convert the unsigned event_flags to a signed value.
    return struct.unpack('q', struct.pack('Q', event.event_flags))[0]


def get_log_level_str(event: EventNotificationMessage) -> str:
    signed_flag = get_signed_event_flag(event)
    # Limit range of flag to mapped level strings.
    flag_idx = max(min(signed_flag, 0), -3)
    return f'{LOG_LEVEL_FLAGS[flag_idx]}({signed_flag})'


class EventNotificationLogger:
    def __init__(self, out_path) -> None:
        self.out_fd = open(out_path, 'w')

    def log_event(self, event: EventNotificationMessage):
        LOG_LEVEL = ''
        if event.event_type == EventType.LOG:
            LOG_LEVEL = '_' + get_log_level_str(event)
        self.out_fd.write(
            f'{event.get_system_time_sec():.1f}: {event.event_type.name}{LOG_LEVEL} - {event.event_description.decode()}\n')
