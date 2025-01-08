from fusion_engine_client.messages import (IMUOutput, InterfaceID, MessageRate,
                                           ProtocolType, TransportType)

from bin.config_tool import apply_message_rate_config


def enable_imu_output(device_interface):
    return apply_message_rate_config(device_interface,
                                     InterfaceID(TransportType.CURRENT),
                                     MessageRate.ON_CHANGE,
                                     ProtocolType.FUSION_ENGINE,
                                     IMUOutput.MESSAGE_TYPE)
