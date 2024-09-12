import json

import numpy as np
from construct import Array, Float32l, Int8ul, Padding, Struct
from fusion_engine_client.messages import DataVersion, PackedDataToBuffer
from fusion_engine_client.utils.construct_utils import AutoEnum
from fusion_engine_client.utils.enum_utils import IntEnum


class CalibrationStage(IntEnum):
    UNKNOWN = 0
    MOUNTING_ANGLE_INITIAL_CONVERGENCE = 1
    MOUNTING_ANGLE_FINAL_CONVERGENCE = 2
    DONE = 255


class CalibrationState:
    VERSION = DataVersion(2, 0)

    Construct = Struct(
        "operating_stage" / AutoEnum(Int8ul, CalibrationStage),
        Padding(3),
        "c_sb" / Array(3, Float32l),
        "c_sb_std_dev" / Array(3, Float32l),
        "travel_distance_m" / Float32l,
        Padding(64),
    )

    def __init__(self):
        self.operating_stage = CalibrationStage.UNKNOWN
        self.c_sb = np.full((3,), np.nan)
        self.c_sb_std_dev = np.full((3,), np.nan)
        self.travel_distance_m = np.nan

    def pack(self, buffer: bytes = None, offset: int = 0, return_buffer: bool = True) -> (bytes, int):
        values = dict(self.__dict__)
        packed_data = self.Construct.build(values)
        return PackedDataToBuffer(packed_data, buffer, offset, return_buffer)

    def unpack(self, buffer: bytes, offset: int = 0) -> int:
        parsed = self.Construct.parse(buffer[offset:])
        self.__dict__.update(parsed)
        del self.__dict__['_io']
        return parsed._io.tell()

    def to_json(self, *args, **kwargs):
        result = self.to_dict()
        return json.dumps(result, *args, **kwargs)

    def from_json(self, str: str):
        result = json.loads(str)
        self.from_dict(result)

    def to_dict(self):
        return {
            '__version': str(self.VERSION),
            'operating_stage': str(self.operating_stage),
            'c_sb': {
                'yaw_deg': self.c_sb[0],
                'pitch_deg': self.c_sb[1],
                'roll_deg': self.c_sb[2],
            },
            'c_sb_std_dev': {
                'yaw_deg': self.c_sb_std_dev[0],
                'pitch_deg': self.c_sb_std_dev[1],
                'roll_deg': self.c_sb_std_dev[2],
            },
            'travel_distance_m': self.travel_distance_m,
        }

    def from_dict(self, contents: dict):
        self.operating_stage = CalibrationStage(contents.get('operating_stage', CalibrationStage.UNKNOWN))
        if 'c_sb' in contents:
            c_sb = contents['c_sb']
            self.c_sb = np.array((c_sb['yaw_deg'], c_sb['pitch_deg'], c_sb['roll_deg']))
        else:
            self.c_sb[:] = np.nan
        if 'c_sb_std_dev' in contents:
            c_sb_std = contents['c_sb_std_dev']
            self.c_sb_std_dev = np.array((c_sb_std['yaw_deg'], c_sb_std['pitch_deg'], c_sb_std['roll_deg']))
        else:
            self.c_sb_std_dev[:] = np.nan
        self.travel_distance_m = contents.get('travel_distance_m', np.nan)
