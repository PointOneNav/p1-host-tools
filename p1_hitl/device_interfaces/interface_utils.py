from argparse import Namespace

from fusion_engine_client.messages import (Direction, IMUOutput, InterfaceID,
                                           MessageRate, ProtocolType,
                                           TransportType)

from bin.config_tool import apply_config, apply_message_rate_config


def enable_imu_output(device_interface, coarse_orientation: tuple[Direction, Direction], save=False) -> bool:
    if not apply_message_rate_config(device_interface,
                                     InterfaceID(TransportType.CURRENT),
                                     MessageRate.ON_CHANGE,
                                     ProtocolType.FUSION_ENGINE,
                                     IMUOutput.MESSAGE_TYPE):
        return False

    args = Namespace(
        param='orientation',
        x=coarse_orientation[0].name.lower(),
        z=coarse_orientation[1].name.lower(),
        save=save)
    return apply_config(device_interface, args)
