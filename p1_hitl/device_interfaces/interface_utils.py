from argparse import Namespace

from fusion_engine_client.messages import (Direction, IMUOutput, InterfaceID,
                                           MessageRate, ProtocolType,
                                           TransportType)

from bin.config_tool import apply_config, apply_message_rate_config


def enable_imu_output(device_interface):
    return apply_message_rate_config(device_interface,
                                     InterfaceID(TransportType.CURRENT),
                                     MessageRate.ON_CHANGE,
                                     ProtocolType.FUSION_ENGINE,
                                     IMUOutput.MESSAGE_TYPE)


def set_imu_orientation(device_interface, coarse_orientation: tuple[Direction, Direction]):
    args = Namespace(param='orientation', x=coarse_orientation[0].name, z=coarse_orientation[0].name)
    return apply_config(device_interface, args)
