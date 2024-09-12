import json

import numpy as np
from construct import Array, Float64l, Int8ul, Padding, Struct
from fusion_engine_client.messages import DataVersion, PackedDataToBuffer
from fusion_engine_client.utils.construct_utils import NumpyAdapter


class StateBlockIndex:
    Construct = Struct(
        "type" / Int8ul,
        "index" / Int8ul,
        Padding(2),
    )

    def __init__(self, type=0xFF, index=0xFF):
        self.type = type
        self.index = index


class GenericFilterState:
    MAX_STATE_BLOCKS = 12
    MAX_STATES = 22

    Construct = Struct(
        "version" / Int8ul,
        "num_state_blocks" / Int8ul,
        "num_states" / Int8ul,
        Padding(1),
        "yaw_mode" / Int8ul,
        "gyro_bias_mode" / Int8ul,
        Padding(2),
        "state_block" / Array(MAX_STATE_BLOCKS, StateBlockIndex.Construct),
        "x" / NumpyAdapter((MAX_STATES,), construct_type=Float64l),
        "variance" / NumpyAdapter((MAX_STATES,), construct_type=Float64l),
    )

    def __init__(self):
        self.yaw_mode = 0
        self.gyro_bias_mode = 0
        self.state_block = []
        self.x = np.array((), dtype=float)
        self.variance = np.array((), dtype=float)

    def to_json(self, *args, **kwargs):
        result = self.to_dict()
        return json.dumps(result, *args, **kwargs)

    def from_json(self, str: str):
        result = json.loads(str)
        self.from_dict(result)

    def to_dict(self, pad_arrays=False):
        def _pad(array, max, type=None):
            if len(array) < max and pad_arrays:
                if isinstance(array, np.ndarray):
                    return np.pad(array, (0, max - len(array)), constant_values=np.nan)
                else:
                    if type is None:
                        type = array[0].__class__
                    return array + [type()] * (max - len(array))
            else:
                return array

        return {
            'version': 0,
            'yaw_mode': self.yaw_mode,
            'gyro_bias_mode': self.gyro_bias_mode,
            'state_block': [{'type': b.type, 'index': b.index}
                            for b in _pad(self.state_block, self.MAX_STATE_BLOCKS, type=StateBlockIndex)],
            'x': _pad(self.x, self.MAX_STATES).tolist(),
            'variance': _pad(self.variance, self.MAX_STATES).tolist(),
        }

    def from_dict(self, contents):
        self.yaw_mode = contents.get('yaw_mode', 0)
        self.gyro_bias_mode = contents.get('gyro_bias_mode', 0)

        self.state_block = []
        for block in contents.get('state_block', []):
            self.state_block.append(StateBlockIndex(type=block.get('type', 0xFF), index=block.get('index', 0xFF)))

        self.x = np.array(contents.get('x', []), dtype=float)
        self.variance = np.array(contents.get('variance', []), dtype=float)

    def from_construct(self, contents):
        self.yaw_mode = contents.yaw_mode
        self.gyro_bias_mode = contents.gyro_bias_mode

        self.state_block = [StateBlockIndex(type=b.type, index=b.index)
                            for b in contents.state_block[:contents.num_state_blocks]]
        self.x = contents.x[:contents.num_states]
        self.variance = contents.x[:contents.num_states]


class TemperatureCompensationAxis:
    Construct = Struct(
        "x" / NumpyAdapter((2,), construct_type=Float64l),
        "cov" / NumpyAdapter((2, 2), construct_type=Float64l),
    )

    def __init__(self):
        self.x = np.full((2,), np.nan)
        self.cov = np.full((2, 2), np.nan)

    def to_dict(self):
        # For this class, covariance is stored in column-major order and all state elements are saved, not just the
        # valid ones.
        return {
            'x': self.x.tolist(),
            'cov': self.cov.reshape((4,), order='C').tolist()
        }

    def from_dict(self, data):
        self.x = np.array(data.get('x', [np.nan] * 2), dtype=float)
        self.cov = np.array(data.get('cov', [np.nan] * 4), dtype=float).reshape((2, 2), order='C')

    def from_construct(self, contents):
        for k in self.__dict__:
            self.__dict__[k] = getattr(contents, k)


class TemperatureCompensationState:
    Construct = Struct(
        "x" / TemperatureCompensationAxis.Construct,
        "y" / TemperatureCompensationAxis.Construct,
        "z" / TemperatureCompensationAxis.Construct,
    )

    def __init__(self):
        self.x = TemperatureCompensationAxis()
        self.y = TemperatureCompensationAxis()
        self.z = TemperatureCompensationAxis()

    def to_dict(self):
        return {k: v.to_dict() for k, v in self.__dict__.items()}

    def from_dict(self, data):
        self.x.from_dict(data.get('x', {}))
        self.y.from_dict(data.get('y', {}))
        self.z.from_dict(data.get('z', {}))

    def from_construct(self, contents):
        self.x.from_construct(contents.x)
        self.y.from_construct(contents.y)
        self.z.from_construct(contents.z)


class WheelScaleFactorState:
    Construct = Struct(
        "x" / NumpyAdapter((4,), construct_type=Float64l),
        "cov" / Array(16, Float64l),
        Padding(32),
    )

    def __init__(self):
        self.x = np.full((4,), np.nan)
        self.cov = np.full((4, 4), np.nan)

    def to_dict(self):
        # Covariance for this class is stored in column-major order, and only the N valid states are stored. For
        # example:
        #  [[1, 3],
        #   [2, 4]]
        # is transmitted as [1, 2, 3, 4].
        num_states = np.sum(~np.isnan(self.x))
        num_elements = num_states ** 2
        return {
            'x': self.x[:num_states].tolist(),
            'cov': self.cov[:num_states, :num_states].reshape((num_elements,), order='C').tolist()
        }

    def from_dict(self, data):
        self.x = np.array(data.get('x', [np.nan] * 4), dtype=float)
        num_states = np.sum(~np.isnan(self.x))
        num_elements = num_states ** 2
        self.cov[:] = np.nan
        self.cov[:num_states, :num_states] = \
            np.array(data.get('cov', [np.nan] * num_elements)[:num_elements], dtype=float) \
            .reshape((num_states, num_states), order='C')
        return True

    def from_construct(self, contents):
        self.from_dict({'x': contents.x, 'cov': contents.cov})


class TightEsrifFilterState:
    VERSION = DataVersion(1, 1)

    Construct = Struct(
        "gps_time_sec" / Float64l,
        "motion_state" / Int8ul,
        Padding(3 + 32),
        "filter_state" / GenericFilterState.Construct,
        Padding(64),
        "temp_comp" / TemperatureCompensationState.Construct,
        Padding(64),
        "ws_state" / WheelScaleFactorState.Construct,
        Padding(64),
    )

    def __init__(self):
        self.gps_time_sec = np.nan
        self.motion_state = 0
        self.filter_state = GenericFilterState()
        self.temp_comp = TemperatureCompensationState()
        self.ws_state = WheelScaleFactorState()

    def pack(self, buffer: bytes = None, offset: int = 0, return_buffer: bool = True) -> (bytes, int):
        values = self.to_dict(pad_arrays=True)
        packed_data = self.Construct.build(values)
        return PackedDataToBuffer(packed_data, buffer, offset, return_buffer)

    def unpack(self, buffer: bytes, offset: int = 0) -> int:
        parsed = self.Construct.parse(buffer[offset:])
        self.from_construct(parsed)
        return parsed._io.tell()

    def to_json(self, *args, **kwargs):
        result = self.to_dict()
        return json.dumps(result, *args, **kwargs)

    def from_json(self, str: str):
        result = json.loads(str)
        self.from_dict(result)

    def to_dict(self, pad_arrays=False):
        return {
            '__version': str(self.VERSION),
            'gps_time_sec': self.gps_time_sec,
            'motion_state': self.motion_state,
            'filter_state': self.filter_state.to_dict(pad_arrays=pad_arrays),
            'temp_comp': self.temp_comp.to_dict(),
            'ws_state': self.ws_state.to_dict(),
        }

    def from_dict(self, contents):
        self.gps_time_sec = contents.get('gps_time_sec', np.nan)
        self.motion_state = contents.get('motion_state', 0)
        self.filter_state.from_dict(contents.get('filter_state', {}))
        self.temp_comp.from_dict(contents.get('temp_comp', {}))
        self.ws_state.from_dict(contents.get('ws_state', {}))

    def from_construct(self, contents):
        self.gps_time_sec = contents.gps_time_sec
        self.motion_state = contents.motion_state
        self.filter_state.from_construct(contents.filter_state)
        self.temp_comp.from_construct(contents.temp_comp)
        self.ws_state.from_construct(contents.ws_state)
