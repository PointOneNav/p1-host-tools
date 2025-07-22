from dataclasses import dataclass, field
import json
from typing import Annotated, Any, Dict, List, Tuple
# typing.Optional aliases construct.Optional
from typing import Optional as OptionalType

from construct import *

from .loader_utilities import AutoEnum, FrozenVectorAdapter, DataClassAdapter, OptionalAdapter, prepare_dataclass_for_json, IntOrStrEnum, update_dataclass_contents

@dataclass
class ProfilingConfig:
    enable_mask: int = 27
    interval_sec: float = 1.0
    context_mask: int = 1

    @staticmethod
    def serialize(val: 'ProfilingConfig') -> bytes:
        return ProfilingConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'ProfilingConfig':
        return ProfilingConfigConstruct.parse(data)

_ProfilingConfigRawConstruct = Struct(
    "enable_mask" / Int8ul,
    Padding(3),
    "interval_sec" / Float32l,
    "context_mask" / Int32ul,
    Padding(40)
)
ProfilingConfigConstruct = DataClassAdapter(ProfilingConfig, _ProfilingConfigRawConstruct)


@dataclass
class Point3f:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @staticmethod
    def serialize(val: 'Point3f') -> bytes:
        return Point3fConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'Point3f':
        return Point3fConstruct.parse(data)

_Point3fRawConstruct = Struct(
    "x" / Float32l,
    "y" / Float32l,
    "z" / Float32l
)
Point3fConstruct = DataClassAdapter(Point3f, _Point3fRawConstruct)


@dataclass
class GpsReceiverExtrinsicsConfig:
    uid: int = -1
    enabled: bool = False
    r_b_bg: Point3f = field(default_factory=lambda:Point3f(**{'x': 0.0, 'y': 0.0, 'z': 0.0}))

    @staticmethod
    def serialize(val: 'GpsReceiverExtrinsicsConfig') -> bytes:
        return GpsReceiverExtrinsicsConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'GpsReceiverExtrinsicsConfig':
        return GpsReceiverExtrinsicsConfigConstruct.parse(data)

_GpsReceiverExtrinsicsConfigRawConstruct = Struct(
    "uid" / Int32sl,
    "enabled" / Flag,
    Padding(3),
    "r_b_bg" / Point3fConstruct,
    Padding(32)
)
GpsReceiverExtrinsicsConfigConstruct = DataClassAdapter(GpsReceiverExtrinsicsConfig, _GpsReceiverExtrinsicsConfigRawConstruct)


@dataclass
class Matrix3x3Float:
    values: Annotated[List[float], 9] = field(default_factory=lambda:[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    @staticmethod
    def serialize(val: 'Matrix3x3Float') -> bytes:
        return Matrix3x3FloatConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'Matrix3x3Float':
        return Matrix3x3FloatConstruct.parse(data)

_Matrix3x3FloatRawConstruct = Struct(
    "values" / Array(9, Float32l)
)
Matrix3x3FloatConstruct = DataClassAdapter(Matrix3x3Float, _Matrix3x3FloatRawConstruct)


@dataclass
class ImuExtrinsicsConfig:
    uid: int = -1
    enabled: bool = False
    r_b_bs: Point3f = field(default_factory=lambda:Point3f(**{'x': 0.0, 'y': 0.0, 'z': 0.0}))
    c_ds: Matrix3x3Float = field(default_factory=lambda:Matrix3x3Float(**{'values': [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]}))

    @staticmethod
    def serialize(val: 'ImuExtrinsicsConfig') -> bytes:
        return ImuExtrinsicsConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'ImuExtrinsicsConfig':
        return ImuExtrinsicsConfigConstruct.parse(data)

_ImuExtrinsicsConfigRawConstruct = Struct(
    "uid" / Int32sl,
    "enabled" / Flag,
    Padding(3),
    "r_b_bs" / Point3fConstruct,
    Padding(20),
    "c_ds" / Matrix3x3FloatConstruct,
    Padding(36)
)
ImuExtrinsicsConfigConstruct = DataClassAdapter(ImuExtrinsicsConfig, _ImuExtrinsicsConfigRawConstruct)


@dataclass
class Rotation3f:
    yaw_deg: float = 0.0
    pitch_deg: float = 0.0
    roll_deg: float = 0.0

    @staticmethod
    def serialize(val: 'Rotation3f') -> bytes:
        return Rotation3fConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'Rotation3f':
        return Rotation3fConstruct.parse(data)

_Rotation3fRawConstruct = Struct(
    "yaw_deg" / Float32l,
    "pitch_deg" / Float32l,
    "roll_deg" / Float32l
)
Rotation3fConstruct = DataClassAdapter(Rotation3f, _Rotation3fRawConstruct)


@dataclass
class ExternalPoseExtrinsicsConfig:
    uid: int = -1
    enabled: bool = False
    r_b_bp: Point3f = field(default_factory=lambda:Point3f(**{'x': 0.0, 'y': 0.0, 'z': 0.0}))
    c_pb: Rotation3f = field(default_factory=lambda:Rotation3f(**{'yaw_deg': 0.0, 'pitch_deg': 0.0, 'roll_deg': 0.0}))

    @staticmethod
    def serialize(val: 'ExternalPoseExtrinsicsConfig') -> bytes:
        return ExternalPoseExtrinsicsConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'ExternalPoseExtrinsicsConfig':
        return ExternalPoseExtrinsicsConfigConstruct.parse(data)

_ExternalPoseExtrinsicsConfigRawConstruct = Struct(
    "uid" / Int32sl,
    "enabled" / Flag,
    Padding(3),
    "r_b_bp" / Point3fConstruct,
    "c_pb" / Rotation3fConstruct,
    Padding(32)
)
ExternalPoseExtrinsicsConfigConstruct = DataClassAdapter(ExternalPoseExtrinsicsConfig, _ExternalPoseExtrinsicsConfigRawConstruct)


@dataclass
class SensorExtrinsicsConfig:
    gps_receivers: List[GpsReceiverExtrinsicsConfig] = field(default_factory=list)
    imus: List[ImuExtrinsicsConfig] = field(default_factory=list)
    external_pose: List[ExternalPoseExtrinsicsConfig] = field(default_factory=list)

    @staticmethod
    def serialize(val: 'SensorExtrinsicsConfig') -> bytes:
        return SensorExtrinsicsConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'SensorExtrinsicsConfig':
        return SensorExtrinsicsConfigConstruct.parse(data)

_SensorExtrinsicsConfigRawConstruct = Struct(
    "gps_receivers" / FrozenVectorAdapter(2, GpsReceiverExtrinsicsConfigConstruct),
    "imus" / FrozenVectorAdapter(1, ImuExtrinsicsConfigConstruct),
    "external_pose" / FrozenVectorAdapter(4, ExternalPoseExtrinsicsConfigConstruct),
    Padding(128)
)
SensorExtrinsicsConfigConstruct = DataClassAdapter(SensorExtrinsicsConfig, _SensorExtrinsicsConfigRawConstruct)


class VehicleModel(IntOrStrEnum):
    UNKNOWN_VEHICLE = 0
    DATASPEED_CD4 = 1
    J1939 = 2
    LEXUS_CT200H = 20
    LEXUS_RX450H = 21
    KIA_SORENTO = 40
    KIA_SPORTAGE = 41
    AUDI_Q7 = 60
    AUDI_A8L = 61
    TESLA_MODEL_X = 80
    TESLA_MODEL_3 = 81
    HYUNDAI_ELANTRA = 100
    PEUGEOT_206 = 120
    MAN_TGX = 140
    FACTION = 160
    FACTION_V2 = 161
    LINCOLN_MKZ = 180
    BMW_7 = 200
    BMW_MOTORRAD = 201
    VW_4 = 220
    RIVIAN = 240
    FLEXRAY_DEVICE_AUDI_ETRON = 260
    ISUZU_F_SERIES = 280

class WheelSensorType(IntOrStrEnum):
    NONE = 0
    TICKS = 2
    WHEEL_SPEED = 3
    VEHICLE_SPEED = 4
    VEHICLE_TICKS = 5

class AppliedSpeedType(IntOrStrEnum):
    NONE = 0
    REAR_WHEELS = 1
    FRONT_WHEELS = 2
    FRONT_AND_REAR_WHEELS = 3
    VEHICLE_BODY = 4

class SteeringType(IntOrStrEnum):
    UNKNOWN = 0
    FRONT = 1
    FRONT_AND_REAR = 2

class TickMode(IntOrStrEnum):
    OFF = 0
    RISING_EDGE = 1
    FALLING_EDGE = 2

class TickDirection(IntOrStrEnum):
    OFF = 0
    FORWARD_ACTIVE_HIGH = 1
    FORWARD_ACTIVE_LOW = 2

@dataclass
class CanConfig:
    id_whitelist: List[int] = field(default_factory=list)
    baudrate: int = 500000

    @staticmethod
    def serialize(val: 'CanConfig') -> bytes:
        return CanConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'CanConfig':
        return CanConfigConstruct.parse(data)

_CanConfigRawConstruct = Struct(
    "id_whitelist" / FrozenVectorAdapter(10, Int32ul),
    "baudrate" / Int32ul,
    Padding(28)
)
CanConfigConstruct = DataClassAdapter(CanConfig, _CanConfigRawConstruct)


@dataclass
class VehicleConfig:
    vehicle_model: VehicleModel = VehicleModel.UNKNOWN_VEHICLE
    wheel_tick_output_interval_sec: float = float("NAN")
    wheelbase_m: float = float("NAN")
    front_track_width_m: float = float("NAN")
    rear_track_width_m: float = float("NAN")
    wheel_sensor_type: WheelSensorType = WheelSensorType.NONE
    applied_speed_type: AppliedSpeedType = AppliedSpeedType.REAR_WHEELS
    steering_type: SteeringType = SteeringType.UNKNOWN
    wheel_update_interval_sec: float = float("NAN")
    steering_ratio: float = float("NAN")
    wheel_ticks_to_m: float = float("NAN")
    wheel_tick_max_value: int = 0
    wheel_ticks_signed: bool = False
    wheel_ticks_always_increase: bool = True
    tick_mode: TickMode = TickMode.OFF
    tick_direction: TickDirection = TickDirection.OFF
    can: CanConfig = field(default_factory=lambda:CanConfig())

    @staticmethod
    def serialize(val: 'VehicleConfig') -> bytes:
        return VehicleConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'VehicleConfig':
        return VehicleConfigConstruct.parse(data)

_VehicleConfigRawConstruct = Struct(
    "vehicle_model" / AutoEnum(Int16ul, VehicleModel),
    Padding(6),
    "wheel_tick_output_interval_sec" / Float32l,
    "wheelbase_m" / Float32l,
    "front_track_width_m" / Float32l,
    "rear_track_width_m" / Float32l,
    "wheel_sensor_type" / AutoEnum(Int8ul, WheelSensorType),
    "applied_speed_type" / AutoEnum(Int8ul, AppliedSpeedType),
    "steering_type" / AutoEnum(Int8ul, SteeringType),
    Padding(1),
    "wheel_update_interval_sec" / Float32l,
    "steering_ratio" / Float32l,
    "wheel_ticks_to_m" / Float32l,
    "wheel_tick_max_value" / Int32ul,
    "wheel_ticks_signed" / Flag,
    "wheel_ticks_always_increase" / Flag,
    "tick_mode" / AutoEnum(Int8ul, TickMode),
    "tick_direction" / AutoEnum(Int8ul, TickDirection),
    "can" / CanConfigConstruct,
    Padding(24)
)
VehicleConfigConstruct = DataClassAdapter(VehicleConfig, _VehicleConfigRawConstruct)


class IonoDelayModel(IntOrStrEnum):
    AUTO = 0
    OFF = 1
    KLOBUCHAR = 2
    SBAS = 3

class TropoDelayModel(IntOrStrEnum):
    AUTO = 0
    OFF = 1
    SAASTAMOINEN = 2

@dataclass
class NavigationConfig:
    r_b_bo: Point3f = field(default_factory=lambda:Point3f(**{'x': 0.0, 'y': 0.0, 'z': 0.0}))
    enu_datum_shift_m: Point3f = field(default_factory=lambda:Point3f(**{'x': 0.0, 'y': 0.0, 'z': 0.0}))
    enable_gps: bool = True
    enable_glonass: bool = True
    enable_galileo: bool = True
    enable_beidou: bool = True
    enable_qzss: bool = True
    enable_sbas: bool = True
    enable_irnss: bool = True
    enable_l1: bool = True
    enable_l2: bool = True
    enable_l5: bool = True
    leap_second: int = 255
    gps_week_rollover: int = 255
    iono_delay_model: IonoDelayModel = IonoDelayModel.AUTO
    tropo_delay_model: TropoDelayModel = TropoDelayModel.AUTO

    @staticmethod
    def serialize(val: 'NavigationConfig') -> bytes:
        return NavigationConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'NavigationConfig':
        return NavigationConfigConstruct.parse(data)

_NavigationConfigRawConstruct = Struct(
    "r_b_bo" / Point3fConstruct,
    "enu_datum_shift_m" / Point3fConstruct,
    "enable_gps" / Flag,
    "enable_glonass" / Flag,
    "enable_galileo" / Flag,
    "enable_beidou" / Flag,
    "enable_qzss" / Flag,
    "enable_sbas" / Flag,
    "enable_irnss" / Flag,
    "enable_l1" / Flag,
    "enable_l2" / Flag,
    "enable_l5" / Flag,
    "leap_second" / Int8ul,
    "gps_week_rollover" / Int8ul,
    "iono_delay_model" / AutoEnum(Int8ul, IonoDelayModel),
    "tropo_delay_model" / AutoEnum(Int8ul, TropoDelayModel),
    Padding(62)
)
NavigationConfigConstruct = DataClassAdapter(NavigationConfig, _NavigationConfigRawConstruct)


class TransportDirection(IntOrStrEnum):
    INVALID = 0
    SERVER = 1
    CLIENT = 2

@dataclass
class TCPInterfaceConfig:
    enable: bool = False
    direction: TransportDirection = TransportDirection.SERVER
    port: int = 0
    hostname: str = ""

    @staticmethod
    def serialize(val: 'TCPInterfaceConfig') -> bytes:
        return TCPInterfaceConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'TCPInterfaceConfig':
        return TCPInterfaceConfigConstruct.parse(data)

_TCPInterfaceConfigRawConstruct = Struct(
    "enable" / Flag,
    "direction" / AutoEnum(Int8ul, TransportDirection),
    "port" / Int16ul,
    "hostname" / PaddedString(64, "utf8"),
    Padding(8)
)
TCPInterfaceConfigConstruct = DataClassAdapter(TCPInterfaceConfig, _TCPInterfaceConfigRawConstruct)


class MessageRate(IntOrStrEnum):
    OFF = 0
    ON_CHANGE = 1
    INTERVAL_10_MS = 2
    INTERVAL_20_MS = 3
    INTERVAL_40_MS = 4
    INTERVAL_50_MS = 5
    INTERVAL_100_MS = 6
    INTERVAL_200_MS = 7
    INTERVAL_500_MS = 8
    INTERVAL_1_S = 9
    INTERVAL_2_S = 10
    INTERVAL_5_S = 11
    INTERVAL_10_S = 12
    INTERVAL_30_S = 13
    INTERVAL_60_S = 14

@dataclass
class FusionEngineMessageRates:
    pose: MessageRate = MessageRate.OFF
    gnss_info: MessageRate = MessageRate.OFF
    gnss_satellite: MessageRate = MessageRate.OFF
    pose_aux: MessageRate = MessageRate.OFF
    calibration_status: MessageRate = MessageRate.OFF
    relative_enu_position: MessageRate = MessageRate.OFF
    imu_output: MessageRate = MessageRate.OFF
    wheel_speed_output: MessageRate = MessageRate.OFF
    vehicle_speed_output: MessageRate = MessageRate.OFF
    raw_wheel_tick_output: MessageRate = MessageRate.OFF
    raw_vehicle_tick_output: MessageRate = MessageRate.OFF
    ros_pose: MessageRate = MessageRate.OFF
    ros_gps_fix: MessageRate = MessageRate.OFF
    ros_imu: MessageRate = MessageRate.OFF
    version_info: MessageRate = MessageRate.OFF
    event_notification: MessageRate = MessageRate.OFF
    raw_gnss_attitude_output: MessageRate = MessageRate.OFF
    system_status: MessageRate = MessageRate.OFF
    raw_imu_output: MessageRate = MessageRate.OFF
    raw_wheel_speed_output: MessageRate = MessageRate.OFF
    raw_vehicle_speed_output: MessageRate = MessageRate.OFF
    device_id: MessageRate = MessageRate.OFF
    ssr_status: MessageRate = MessageRate.OFF
    gnss_attitude_output: MessageRate = MessageRate.OFF
    gnss_signals: MessageRate = MessageRate.OFF

    @staticmethod
    def serialize(val: 'FusionEngineMessageRates') -> bytes:
        return FusionEngineMessageRatesConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'FusionEngineMessageRates':
        return FusionEngineMessageRatesConstruct.parse(data)

_FusionEngineMessageRatesRawConstruct = Struct(
    "pose" / AutoEnum(Int8ul, MessageRate),
    "gnss_info" / AutoEnum(Int8ul, MessageRate),
    "gnss_satellite" / AutoEnum(Int8ul, MessageRate),
    "pose_aux" / AutoEnum(Int8ul, MessageRate),
    "calibration_status" / AutoEnum(Int8ul, MessageRate),
    "relative_enu_position" / AutoEnum(Int8ul, MessageRate),
    "imu_output" / AutoEnum(Int8ul, MessageRate),
    "wheel_speed_output" / AutoEnum(Int8ul, MessageRate),
    "vehicle_speed_output" / AutoEnum(Int8ul, MessageRate),
    "raw_wheel_tick_output" / AutoEnum(Int8ul, MessageRate),
    "raw_vehicle_tick_output" / AutoEnum(Int8ul, MessageRate),
    "ros_pose" / AutoEnum(Int8ul, MessageRate),
    "ros_gps_fix" / AutoEnum(Int8ul, MessageRate),
    "ros_imu" / AutoEnum(Int8ul, MessageRate),
    "version_info" / AutoEnum(Int8ul, MessageRate),
    "event_notification" / AutoEnum(Int8ul, MessageRate),
    "raw_gnss_attitude_output" / AutoEnum(Int8ul, MessageRate),
    "system_status" / AutoEnum(Int8ul, MessageRate),
    "raw_imu_output" / AutoEnum(Int8ul, MessageRate),
    "raw_wheel_speed_output" / AutoEnum(Int8ul, MessageRate),
    "raw_vehicle_speed_output" / AutoEnum(Int8ul, MessageRate),
    "device_id" / AutoEnum(Int8ul, MessageRate),
    "ssr_status" / AutoEnum(Int8ul, MessageRate),
    "gnss_attitude_output" / AutoEnum(Int8ul, MessageRate),
    "gnss_signals" / AutoEnum(Int8ul, MessageRate),
    Padding(15)
)
FusionEngineMessageRatesConstruct = DataClassAdapter(FusionEngineMessageRates, _FusionEngineMessageRatesRawConstruct)


@dataclass
class NMEAMessageRates:
    gga: MessageRate = MessageRate.OFF
    gll: MessageRate = MessageRate.OFF
    gsa: MessageRate = MessageRate.OFF
    gsv: MessageRate = MessageRate.OFF
    rmc: MessageRate = MessageRate.OFF
    vtg: MessageRate = MessageRate.OFF
    p1calstatus: MessageRate = MessageRate.OFF
    p1msg: MessageRate = MessageRate.OFF
    pqtmverno: MessageRate = MessageRate.OFF
    pqtmver: MessageRate = MessageRate.OFF
    pqtmgnss: MessageRate = MessageRate.OFF
    pqtmverno_sub: MessageRate = MessageRate.OFF
    pqtmver_sub: MessageRate = MessageRate.OFF
    pqtmtxt: MessageRate = MessageRate.OFF
    zda: MessageRate = MessageRate.OFF

    @staticmethod
    def serialize(val: 'NMEAMessageRates') -> bytes:
        return NMEAMessageRatesConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'NMEAMessageRates':
        return NMEAMessageRatesConstruct.parse(data)

_NMEAMessageRatesRawConstruct = Struct(
    "gga" / AutoEnum(Int8ul, MessageRate),
    "gll" / AutoEnum(Int8ul, MessageRate),
    "gsa" / AutoEnum(Int8ul, MessageRate),
    "gsv" / AutoEnum(Int8ul, MessageRate),
    "rmc" / AutoEnum(Int8ul, MessageRate),
    "vtg" / AutoEnum(Int8ul, MessageRate),
    "p1calstatus" / AutoEnum(Int8ul, MessageRate),
    "p1msg" / AutoEnum(Int8ul, MessageRate),
    "pqtmverno" / AutoEnum(Int8ul, MessageRate),
    "pqtmver" / AutoEnum(Int8ul, MessageRate),
    "pqtmgnss" / AutoEnum(Int8ul, MessageRate),
    "pqtmverno_sub" / AutoEnum(Int8ul, MessageRate),
    "pqtmver_sub" / AutoEnum(Int8ul, MessageRate),
    "pqtmtxt" / AutoEnum(Int8ul, MessageRate),
    "zda" / AutoEnum(Int8ul, MessageRate),
    Padding(1)
)
NMEAMessageRatesConstruct = DataClassAdapter(NMEAMessageRates, _NMEAMessageRatesRawConstruct)


@dataclass
class RTCMMessageRates:
    position: MessageRate = MessageRate.OFF
    msm: MessageRate = MessageRate.OFF

    @staticmethod
    def serialize(val: 'RTCMMessageRates') -> bytes:
        return RTCMMessageRatesConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'RTCMMessageRates':
        return RTCMMessageRatesConstruct.parse(data)

_RTCMMessageRatesRawConstruct = Struct(
    "position" / AutoEnum(Int8ul, MessageRate),
    "msm" / AutoEnum(Int8ul, MessageRate),
    Padding(38)
)
RTCMMessageRatesConstruct = DataClassAdapter(RTCMMessageRates, _RTCMMessageRatesRawConstruct)


@dataclass
class ProtocolMessageRates:
    fusion_engine_rates: FusionEngineMessageRates = field(default_factory=lambda:FusionEngineMessageRates())
    nmea_rates: NMEAMessageRates = field(default_factory=lambda:NMEAMessageRates())
    rtcm_rates: RTCMMessageRates = field(default_factory=lambda:RTCMMessageRates())
    diagnostic_messages_enabled: bool = False

    @staticmethod
    def serialize(val: 'ProtocolMessageRates') -> bytes:
        return ProtocolMessageRatesConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'ProtocolMessageRates':
        return ProtocolMessageRatesConstruct.parse(data)

_ProtocolMessageRatesRawConstruct = Struct(
    "fusion_engine_rates" / FusionEngineMessageRatesConstruct,
    "nmea_rates" / NMEAMessageRatesConstruct,
    "rtcm_rates" / RTCMMessageRatesConstruct,
    "diagnostic_messages_enabled" / Flag,
    Padding(3)
)
ProtocolMessageRatesConstruct = DataClassAdapter(ProtocolMessageRates, _ProtocolMessageRatesRawConstruct)


@dataclass
class TCPSocketConfig:
    interface_config: TCPInterfaceConfig = field(default_factory=lambda:TCPInterfaceConfig())
    output_rates: ProtocolMessageRates = field(default_factory=lambda:ProtocolMessageRates())

    @staticmethod
    def serialize(val: 'TCPSocketConfig') -> bytes:
        return TCPSocketConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'TCPSocketConfig':
        return TCPSocketConfigConstruct.parse(data)

_TCPSocketConfigRawConstruct = Struct(
    "interface_config" / TCPInterfaceConfigConstruct,
    "output_rates" / ProtocolMessageRatesConstruct
)
TCPSocketConfigConstruct = DataClassAdapter(TCPSocketConfig, _TCPSocketConfigRawConstruct)


@dataclass
class FileInterfaceConfig:
    enable: bool = False
    filename: str = ""

    @staticmethod
    def serialize(val: 'FileInterfaceConfig') -> bytes:
        return FileInterfaceConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'FileInterfaceConfig':
        return FileInterfaceConfigConstruct.parse(data)

_FileInterfaceConfigRawConstruct = Struct(
    "enable" / Flag,
    Padding(3),
    "filename" / PaddedString(64, "utf8")
)
FileInterfaceConfigConstruct = DataClassAdapter(FileInterfaceConfig, _FileInterfaceConfigRawConstruct)


@dataclass
class FileOutputConfig:
    interface_config: FileInterfaceConfig = field(default_factory=lambda:FileInterfaceConfig())
    output_rates: ProtocolMessageRates = field(default_factory=lambda:ProtocolMessageRates())

    @staticmethod
    def serialize(val: 'FileOutputConfig') -> bytes:
        return FileOutputConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'FileOutputConfig':
        return FileOutputConfigConstruct.parse(data)

_FileOutputConfigRawConstruct = Struct(
    "interface_config" / FileInterfaceConfigConstruct,
    "output_rates" / ProtocolMessageRatesConstruct
)
FileOutputConfigConstruct = DataClassAdapter(FileOutputConfig, _FileOutputConfigRawConstruct)


@dataclass
class UDPInterfaceConfig:
    enable: bool = False
    hostname: str = ""
    port: int = 0

    @staticmethod
    def serialize(val: 'UDPInterfaceConfig') -> bytes:
        return UDPInterfaceConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'UDPInterfaceConfig':
        return UDPInterfaceConfigConstruct.parse(data)

_UDPInterfaceConfigRawConstruct = Struct(
    "enable" / Flag,
    "hostname" / PaddedString(64, "utf8"),
    Padding(9),
    "port" / Int16ul
)
UDPInterfaceConfigConstruct = DataClassAdapter(UDPInterfaceConfig, _UDPInterfaceConfigRawConstruct)


@dataclass
class UDPSocketConfig:
    interface_config: UDPInterfaceConfig = field(default_factory=lambda:UDPInterfaceConfig())
    output_rates: ProtocolMessageRates = field(default_factory=lambda:ProtocolMessageRates())

    @staticmethod
    def serialize(val: 'UDPSocketConfig') -> bytes:
        return UDPSocketConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'UDPSocketConfig':
        return UDPSocketConfigConstruct.parse(data)

_UDPSocketConfigRawConstruct = Struct(
    "interface_config" / UDPInterfaceConfigConstruct,
    "output_rates" / ProtocolMessageRatesConstruct
)
UDPSocketConfigConstruct = DataClassAdapter(UDPSocketConfig, _UDPSocketConfigRawConstruct)


@dataclass
class CommInterfacesConfig:
    tcp_sockets: Annotated[List[TCPSocketConfig], 5] = field(default_factory=lambda:[TCPSocketConfig(), TCPSocketConfig(), TCPSocketConfig(), TCPSocketConfig(), TCPSocketConfig()])
    web_sockets: Annotated[List[TCPSocketConfig], 1] = field(default_factory=lambda:[TCPSocketConfig()])
    file_outputs: Annotated[List[FileOutputConfig], 2] = field(default_factory=lambda:[FileOutputConfig(), FileOutputConfig()])
    udp_sockets: Annotated[List[UDPSocketConfig], 2] = field(default_factory=lambda:[UDPSocketConfig(), UDPSocketConfig()])

    @staticmethod
    def serialize(val: 'CommInterfacesConfig') -> bytes:
        return CommInterfacesConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'CommInterfacesConfig':
        return CommInterfacesConfigConstruct.parse(data)

_CommInterfacesConfigRawConstruct = Struct(
    "tcp_sockets" / Array(5, TCPSocketConfigConstruct),
    "web_sockets" / Array(1, TCPSocketConfigConstruct),
    "file_outputs" / Array(2, FileOutputConfigConstruct),
    "udp_sockets" / Array(2, UDPSocketConfigConstruct),
    Padding(284)
)
CommInterfacesConfigConstruct = DataClassAdapter(CommInterfacesConfig, _CommInterfacesConfigRawConstruct)


@dataclass
class SystemControlConfig:
    enable_watchdog_timer: bool = True
    device_id: str = ""

    @staticmethod
    def serialize(val: 'SystemControlConfig') -> bytes:
        return SystemControlConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'SystemControlConfig':
        return SystemControlConfigConstruct.parse(data)

_SystemControlConfigRawConstruct = Struct(
    "enable_watchdog_timer" / Flag,
    Padding(3),
    "device_id" / PaddedString(32, "utf8"),
    Padding(96)
)
SystemControlConfigConstruct = DataClassAdapter(SystemControlConfig, _SystemControlConfigRawConstruct)


@dataclass
class UserConfig:
    profiling: ProfilingConfig = field(default_factory=lambda:ProfilingConfig())
    sensors: SensorExtrinsicsConfig = field(default_factory=lambda:SensorExtrinsicsConfig())
    vehicle: VehicleConfig = field(default_factory=lambda:VehicleConfig())
    navigation: NavigationConfig = field(default_factory=lambda:NavigationConfig())
    comm_interfaces: CommInterfacesConfig = field(default_factory=lambda:CommInterfacesConfig())
    system_controls: SystemControlConfig = field(default_factory=lambda:SystemControlConfig())

    @staticmethod
    def serialize(val: 'UserConfig') -> bytes:
        return UserConfigConstruct.build(val)

    @staticmethod
    def deserialize(data: bytes) -> 'UserConfig':
        return UserConfigConstruct.parse(data)

    EXTRA_JSON_DATA = {
        "__version": "7.7",
        "__platform_id": 1
    }

    @staticmethod
    def get_version() -> Tuple[int, int]:
        """
        Ver 3 - Added serial port configuration support at the end of the output_interfaces member.
        Ver 3.1 - Modified fields in VehicleConfig.
        Ver 4.0 - Replaced protocol_output_mappings with generated output_rates.
        Ver 4.1 - Added system_controls with enable_watchdog_timer.
        Ver 4.2 - Update to force enable watchdog on current devices. 8/31/2022
        Ver 4.3 - Update to include NMEA PQTMTXT type. 1/31/2023
        Ver 4.4 - Update to force enable heading msg on current devices. 2/8/2023
        Ver 4.5 - Update to enable system profiling context. 2/14/2023
        Ver 5.0 - Major change to drop unused features and reserve space. 2/22/2023
        Ver 6.0 - Major refactor of IO interfaces. 3/10/2023
        Ver 6.1 - Added UTC leap second manual override. 3/21/2023
        Ver 6.2 - Added week rollover manual override. 3/24/2023
        Ver 6.3 - Added ionospheric and tropospheric delay model configurations. 4/20/2023
        Ver 6.4 - Added heading vertical and horizontal bias configurations. 5/22/2023
        Ver 6.5 - Added device ID. 6/06/2023
        Ver 6.6 - Added CAN baudrate. 6/07/2023
        Ver 6.7 - Removed WheelSensorType::TICK_RATE enum. 9/25/2023
        Ver 6.8 - Removed comm_interfaces.ui and added udp_clients. 1/8/2024
        Ver 6.9 - Removed diagnostic log from comm_interfaces.file_output and UI websocket from comm_interfaces.websocket_servers. 2/6/2024
        Ver 6.10 - Added UNIX domain socket support (not currently enabled for any platforms). 5/17/2024
        Ver 7.0 - Combined UDP and TCP client/server configuration to match UNIX sockets. 5/30/2024
        Ver 7.1 - Changing profiler enable value to enable_mask. 6/26/2024
        Ver 7.2 - Added (corrected) heading output message rate control. 11/22/2024
        Ver 7.3 - Removed heading config (biases) in favor of lever arm in second GNSS
                  receiver entry. 12/3/2024
        Ver 7.4 - Added NMEA ZDA message rate control. 12/14/2024
        Ver 7.5 - Added FLEXRAY_DEVICE_AUDI_ETRON VehicleModel enum. 04/02/2025
        Ver 7.6 - Added GNSSSignalsMessage. 05/12/2025
        Ver 7.7 - Added Isuzu F-Series vehicle model enum. 6/25/2025

        """
        return 7, 7

    @staticmethod
    def get_platform_id() -> int:
        return 1

    @staticmethod
    def get_serialized_size() -> int:
        return UserConfigConstruct.sizeof()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UserConfig':
        config = cls()
        config.update(data)
        return config

    def update(self, other) -> Dict[str, Any]:
        """!
        See update_dataclass_contents()
        """
        return update_dataclass_contents(self, other)

    def to_dict(self) ->  Dict[str, Any]:
        return prepare_dataclass_for_json(self)

    def to_json(self) -> str:
        dict_contents = self.to_dict()
        dict_contents.update(self.EXTRA_JSON_DATA)
        return json.dumps(dict_contents, indent=2, sort_keys=True)

_UserConfigRawConstruct = Struct(
    "profiling" / ProfilingConfigConstruct,
    "sensors" / SensorExtrinsicsConfigConstruct,
    "vehicle" / VehicleConfigConstruct,
    "navigation" / NavigationConfigConstruct,
    "comm_interfaces" / CommInterfacesConfigConstruct,
    "system_controls" / SystemControlConfigConstruct,
    Padding(6464),
    Padding(1136)
)
UserConfigConstruct = DataClassAdapter(UserConfig, _UserConfigRawConstruct)

