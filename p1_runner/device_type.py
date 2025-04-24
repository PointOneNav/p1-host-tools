import re
from enum import Enum, auto
from typing import Dict, Optional


class DeviceType(Enum):
    UNKNOWN = auto()

    AMAZON_FLEETEDGE_V1 = auto()
    ATLAS = auto()
    BMW_MOTO_MIC = auto()
    LG69T_AM = auto()
    LG69T_AP = auto()
    LG69T_AH = auto()
    LG69T_AJ = auto()
    ZIPLINE = auto()
    P1_LG69T_GNSS = auto()

    # NOTE: For now HITL will use ST_TESEO_HEADING_PRIMARY to refer to a complete heading DUT comprised of both a
    # primary and secondary device.
    ST_TESEO_HEADING_PRIMARY = auto()
    ST_TESEO_HEADING_SECONDARY = auto()

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

    def is_gnss_only(self) -> bool:
        return self in (DeviceType.LG69T_AM, DeviceType.ZIPLINE,
                        DeviceType.P1_LG69T_GNSS, DeviceType.ST_TESEO_HEADING_PRIMARY)

    def has_attitude(self) -> bool:
        return self in (DeviceType.ST_TESEO_HEADING_PRIMARY,)

    @classmethod
    def mapping_device_to_regex(cls) -> Dict['DeviceType', str]:
        return {
            DeviceType.ATLAS: 'v[0-9]*.*',
            DeviceType.LG69T_AM: 'lg69t-am-v[0-9]*.*',
            DeviceType.LG69T_AP: 'lg69t-ap-v[0-9]*.*',
            DeviceType.ZIPLINE: 'zipline-v[0-9]*.*',
            DeviceType.AMAZON_FLEETEDGE_V1: 'amazon-fleetedge-1-v[0-9]*.*',
            DeviceType.BMW_MOTO_MIC: 'bmw-moto-mic-v[0-9]*.*',
            DeviceType.P1_LG69T_GNSS: 'p1-lg69t-gnss-v[0-9]*.*',
            DeviceType.ST_TESEO_HEADING_PRIMARY: 'teseo-heading-v[0-9]*.*',
        }

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
        mapping = cls.mapping_device_to_regex()
        for key, val in mapping.items():
            r = fr'{val}'
            if re.match(r, version_str):
                return key

        return None
