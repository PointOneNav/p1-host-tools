from argparse import Namespace
from typing import Optional, Type

from fusion_engine_client.messages import (CommandResponseMessage,
                                           ConfigurationSource, DataType,
                                           DataVersion, ImportDataMessage,
                                           PlatformStorageDataMessage,
                                           Response)

from bin.config_tool import query_fe_version
from p1_runner import trace as logging
from p1_runner.device_interface import DeviceInterface
from p1_runner.import_config_loader import (UserConfigType,
                                            get_config_loader_class)

logger = logging.getLogger('point_one.config_loader_helper')


def user_config_from_platform_storage(storage: PlatformStorageDataMessage,
                                      UserConfig: Type[UserConfigType]) -> Optional[UserConfigType]:
    if storage.data_type == DataType.USER_CONFIG:
        if not UserConfig.get_version() == storage.data_version:
            logger.warning(
                "UserConfig Python library version %s doesn't match exported data %s. Skipping JSON for UserConfig export.",
                UserConfig.get_version(), storage.data_version)
        # get_serialized_size wasn't present in early versions of UserConfig.
        elif hasattr(UserConfig, 'get_serialized_size') and len(storage.data) != UserConfig.get_serialized_size():
            logger.warning(
                f"Storage size did not match known configuration. [size={len(storage.data)} known={UserConfig.get_serialized_size()}]")
        else:
            try:
                user_config = UserConfig.deserialize(storage.data)
                return user_config
            except Exception as e:
                logger.warning("Problem loading UserConfig: %s", str(e))
    else:
        logger.warning("Storage type was not UserConfig.")
    return None


def get_config_loader_for_device(config_interface: DeviceInterface,
                                 args: Optional[Namespace] = None) -> Optional[Type[UserConfigType]]:
    resp = query_fe_version(config_interface, None)
    if resp is not None:
        try:
            return get_config_loader_class(args, resp.engine_version_str)
        except Exception as e:
            logger.error(f'Could not get UserConfig loader {type(e).__name__}: "{e}"')

    return None


def device_import_user_config(config_interface: DeviceInterface, user_config: UserConfigType,
                              source=ConfigurationSource.ACTIVE) -> bool:
    import_cmd = ImportDataMessage(data_type=DataType.USER_CONFIG, data_version=DataVersion(
        *user_config.get_version()), data=user_config.serialize(user_config), source=source)
    config_interface.send_message(import_cmd)

    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
    if not isinstance(resp, CommandResponseMessage):
        logger.error('Device did not respond to import request.')
        return False
    elif resp.response != Response.OK:
        logger.error('Import command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
        return False
    else:
        return True
