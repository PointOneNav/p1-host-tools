import struct
import time
from enum import IntEnum
from typing import Union


class WebsocketDataType(IntEnum):
    DATA_TYPE_UNKNOWN = 0
    DATA_TYPE_MATRIX = 1
    DATA_TYPE_STRING = 2
    DATA_TYPE_PROTO = 3
    DATA_TYPE_NMEA = 4


class WebsocketHeader(object):
    _FORMAT = '<Id'
    _SIZE: int = struct.calcsize(_FORMAT)

    def __init__(self):
        self.data_type = WebsocketDataType.DATA_TYPE_NMEA
        self.timestamp = time.time()

    def pack(self, buffer: bytes = None, offset: int = 0, return_buffer: bool = False) -> Union[bytes, int]:
        if buffer is None:
            buffer = struct.pack(WebsocketHeader._FORMAT, self.data_type, self.timestamp)
        else:
            struct.pack_into(WebsocketHeader._FORMAT, buffer, self.data_type, self.timestamp)

        if return_buffer:
            return buffer
        else:
            return self.calcsize()

    @classmethod
    def calcsize(cls) -> int:
        return cls._SIZE
