from enum import auto, Enum
from typing import Optional


class DeviceType(Enum):
    UNKNOWN = auto()

    LG69T_AM = auto()
    LG69T_AP = auto()
    LG69T_AH = auto()
    LG69T_AJ = auto()
    ATLAS = auto()

    BEAM2K = auto()
    DJI_MAVIC = auto()

    GENERIC_RTCM = auto()
    ST_TESEO = auto()
    LC29H = auto()
    SEPTENTRIO = auto()
    UBLOX = auto()

    def is_lg69t(self) -> bool:
        return self in (DeviceType.LG69T_AH, DeviceType.LG69T_AM, DeviceType.LG69T_AP)

    def device_uses_unframed_logs(self) -> bool:
        return self.is_lg69t() or self is DeviceType.LC29H

    @classmethod
    def from_string(cls, name: Optional[str]) -> 'DeviceType':
        if name is not None:
            try:
                return cls[name]
            except Exception as e:
                pass

        return DeviceType.UNKNOWN
