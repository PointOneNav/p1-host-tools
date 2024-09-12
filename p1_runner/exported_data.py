import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from enum import Enum, auto
from typing import IO, List, NamedTuple, Optional, Tuple
from zipfile import ZipFile

from fusion_engine_client.messages import (DataType, DataVersion, DeviceType,
                                           ImportDataMessage,
                                           PlatformStorageDataMessage,
                                           Response, VersionInfoMessage)

# If this running in the development repo, try updating the UserConfig
# definitions. This will generate separate quectel_user_config_loader.py and
# atlas_user_config_loader.py to support the different configuration structs.
update_user_config_script = os.path.normpath(
    os.path.join(os.path.dirname(__file__),
                 '../../scripts/update_user_config_loader.sh'))
if os.path.exists(update_user_config_script):
    subprocess.run(update_user_config_script)


class ConfigType(Enum):
    QUECTEL = auto()
    ATLAS = auto()


try:
    repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
    sys.path.append(repo_root)

    from user_config_loader.atlas_user_config_loader import \
        UserConfig as AtlasUserConfig
    from user_config_loader.quectel_user_config_loader import \
        UserConfig as QuectelUserConfig

    _CONFIG_CLASSES = {
        ConfigType.QUECTEL: QuectelUserConfig,
        ConfigType.ATLAS: AtlasUserConfig,
    }

    # The configuration structs can be identified by the size of their binary
    # serialized data.
    _CONFIG_SIZES = {v.get_serialized_size(): k for k,
                     v in _CONFIG_CLASSES.items()}

    # Modify the classes so that they will include the correct ConfigType when
    # serializing to JSON.
    for k, v in _CONFIG_CLASSES.items():
        v.EXTRA_JSON_DATA['__device'] = k.name

    __has_user_config_loader = True
    __user_config_version = DataVersion(*QuectelUserConfig.get_version())
except:
    __has_user_config_loader = False

logger = logging.getLogger('point_one.exported_data')


def user_config_version_match(other: DataVersion) -> bool:
    return __user_config_version.major == other.major and __user_config_version.minor == other.minor


def is_export_valid(save_file: str) -> bool:
    try:
        with ZipFile(save_file, 'r') as export_zip:
            return True
    except:
        return False


def user_config_from_platform_storage(storage: PlatformStorageDataMessage) -> Optional['UserConfig']:
    if storage.data_type == DataType.USER_CONFIG:
        if not __has_user_config_loader:
            logger.warning(
                "No UserConfig Python library available. Skipping JSON for UserConfig export.")
        elif not user_config_version_match(storage.data_version):
            logger.warning(
                "UserConfig Python library version %s doesn't match exported data %s. Skipping JSON for UserConfig export.",
                __user_config_version, storage.data_version)
        else:
            config_type = _CONFIG_SIZES.get(len(storage.data))
            if config_type is not None:
                ConfigClass = _CONFIG_CLASSES[config_type]
                try:
                    user_config = ConfigClass.deserialize(storage.data)
                    return user_config
                except Exception as e:
                    logger.warning("Problem loading UserConfig: %s", str(e))
            else:
                logger.warning(
                    f"Storage size did not match known configuration. [size={len(storage.data)} known={_CONFIG_SIZES}]")
    else:
        logger.warning("Storage type was not UserConfig.")
    return None


def create_exported_data(save_file: str, version: VersionInfoMessage):
    meta_data = {
        'timestamp': datetime.utcnow().isoformat(),
        'device_version': {
            'firmware': version.fw_version_str,
            'fusion_engine': version.engine_version_str,
            'os': version.os_version_str,
            'gnss_reciever': version.rx_version_str
        }
    }
    logger.info('Creating new export archive: "%s"', save_file)
    with ZipFile(save_file, 'w') as export_zip:
        export_zip.writestr('meta.json', json.dumps(
            meta_data, indent=2, sort_keys=True))


def add_to_exported_data(save_file: str, exported_data: PlatformStorageDataMessage):
    file_prefix = f'{exported_data.data_type.name}-{exported_data.data_version.major}_{exported_data.data_version.minor}-{exported_data.response.name}'
    with ZipFile(save_file, 'a') as export_zip:
        if exported_data.data_type == DataType.USER_CONFIG:
            user_config = user_config_from_platform_storage(exported_data)
            if user_config:
                logger.info('Creating JSON save of exported %s',
                            exported_data.data_type.name)
                export_zip.writestr(file_prefix + '.json',
                                    user_config.to_json())

        logger.info('Creating binary save of exported %s',
                    exported_data.data_type.name)
        export_zip.writestr(file_prefix + '.bin', exported_data.data)


class _ExportInfo(NamedTuple):
    type: DataType
    version: DataVersion
    validity: Response
    encoding: str
    file: str


def _find_match(exported_data: List[_ExportInfo], type: DataType, encoding: str) -> Optional[_ExportInfo]:
    for info in exported_data:
        if info.type == type and info.encoding == encoding:
            return info
    return None


def load_json_user_config_data(json_fd: IO, config_type: Optional[ConfigType] = None, default_data: Optional[PlatformStorageDataMessage] = None) -> Optional[bytes]:
    if not __has_user_config_loader:
        logger.error(
            "No UserConfig Python library available. Skipping JSON for UserConfig import.")
        return None

    try:
        json_data = json.load(json_fd)
        if "__version" in json_data:
            versions = json_data["__version"].split(".")
            file_version = DataVersion(int(versions[0]), int(versions[1]))
            if not user_config_version_match(file_version):
                logger.warning("JSON file version %s did not match UserConfig library version %s. "
                               "Remove '__version' from the JSON to try loading anyway, or use a version of config_tools"
                               " that matches the file Version.", file_version, __user_config_version)
                return None

        if config_type is None:
            if "__device" in json_data:
                try:
                    config_type = ConfigType[json_data["__device"]]
                except KeyError:
                    logger.warning(
                        f'Invalid ConfigType{json_data["__device"]}.')
            else:
                logger.warning(
                    f"JSON was exported by an unspecified device type. Assuming it was generated by a Quectel device. To specify the device, add a '__device' key to the JSON with the ConfigType.")
                config_type = ConfigType.QUECTEL

        ConfigClass = _CONFIG_CLASSES[config_type]

        if default_data:
            loaded_config = ConfigClass.deserialize(default_data.data)
            # Try to merge lists containing complex types (e.g. IMU configuration).
            loaded_config.update(json_data)
        else:
            loaded_config = ConfigClass.from_dict(json_data)
        return ConfigClass.serialize(loaded_config)
    except Exception as e:
        logger.warning("Problem loading UserConfig: %s", str(e))
        return None


def load_saved_json(save_file: str, data_type: DataType, default_data: Optional[PlatformStorageDataMessage] = None) -> List[Tuple[ImportDataMessage, Response]]:
    imports = []
    if data_type != DataType.USER_CONFIG:
        logger.error('JSON files only supported for USER_CONFIG DataType.')
    elif not __has_user_config_loader:
        logger.error(
            "No UserConfig Python library available. Skipping JSON for UserConfig import.")
    elif default_data is not None and not user_config_version_match(default_data.data_version):
        logger.warning(
            "UserConfig Python library version %s doesn't match exported data %s. Skipping JSON for UserConfig export.",
            __user_config_version, default_data.data_version)
    else:
        with open(save_file, 'r') as fd:
            data = load_json_user_config_data(fd, default_data)
        if data != None:
            imports.append((ImportDataMessage(data_type=DataType.USER_CONFIG,
                            data_version=__user_config_version, data=data), Response.OK))

    return imports


def load_saved_data(save_file: str, types: List[DataType]) -> List[Tuple[ImportDataMessage, Response]]:
    imports = []
    file_info = []
    with ZipFile(save_file, 'r') as export_zip:
        exported_files = export_zip.namelist()
        logger.debug('Archive files: %s', exported_files)
        name_re = re.compile(
            r'([A-Z_]+)-([0-9]+)_([0-9]+)-([A-Z_]+)\.([a-z]+)')
        for exported_file in exported_files:
            m = name_re.match(exported_file)
            if m is not None:
                file_info.append(_ExportInfo(
                    DataType[m.group(1)],
                    DataVersion(
                        int(m.group(2)),
                        int(m.group(3))),
                    Response[m.group(4)],
                    m.group(5),
                    exported_file
                ))

        for file_data_type in types:
            data = None
            export_info = None
            if file_data_type == DataType.USER_CONFIG:
                export_info = _find_match(
                    file_info, DataType.USER_CONFIG, 'json')
                if export_info is not None:
                    if not __has_user_config_loader:
                        logger.warning(
                            "No UserConfig Python library available. Skipping JSON for UserConfig import.")
                    elif not user_config_version_match(export_info.version):
                        logger.warning(
                            "UserConfig Python library version %s doesn't match exported data %s. Skipping JSON for UserConfig export.",
                            __user_config_version, export_info.version)
                    else:
                        logger.info(
                            'Importing JSON save of exported UserConfig: %s', export_info.file)
                        with export_zip.open(export_info.file, 'r') as fd:
                            data = load_json_user_config_data(fd)

            if data is None:
                export_info = _find_match(
                    file_info, file_data_type, 'bin')
                if export_info is not None:
                    if export_info.validity == Response.NO_DATA_STORED:
                        data = bytes()
                    else:
                        logger.info(
                            'Importing binary save of exported data: %s', export_info.file)
                        # This is a binary file, but `rb` appears to not supported.
                        with export_zip.open(export_info.file, 'r') as fd:
                            data = fd.read()

            if data is not None and export_info is not None:
                imports.append((ImportDataMessage(
                    data_type=export_info.type, data_version=export_info.version, data=data), export_info.validity))

    return imports
