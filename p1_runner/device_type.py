import re
from enum import Enum, auto
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
    ZIPLINE = auto()

    ZIPLINE = auto()

    def is_lg69t(self) -> bool:
        return self in (DeviceType.LG69T_AH, DeviceType.LG69T_AM, DeviceType.LG69T_AP)

    def is_zipline(self) -> bool:
        return self is DeviceType.ZIPLINE

    def device_uses_unframed_logs(self) -> bool:
        return self.is_lg69t() or self is DeviceType.LC29H

    def is_gnss_only(self) -> bool:
        return self in (DeviceType.LG69T_AM, DeviceType.ZIPLINE)

    @classmethod
    def from_string(cls, name: Optional[str]) -> 'DeviceType':
        if name is not None:
            try:
                return cls[name]
            except Exception as e:
                pass

        return DeviceType.UNKNOWN

    @classmethod
    def get_build_type_from_version(cls, version_str) -> Optional['DeviceType']:
        # Determine path to the auto-generated config loading code on S3.
        if re.match(r'lg69t-am-', version_str):
            return DeviceType.LG69T_AM
        elif re.match(r'v\d+\.\d+\.\d+', version_str):
            return DeviceType.ATLAS
        else:
            return None
