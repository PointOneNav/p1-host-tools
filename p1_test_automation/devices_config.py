import copy
import json
import logging
import re
import socket
import sys
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional

import serial
from pydantic import BaseModel, ConfigDict

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_runner.data_source import (DataSource, SerialDataSource,
                                   SocketDataSource)
from p1_runner.find_serial_device import PortType, find_serial_device

logger = logging.getLogger('point_one.test_automation.device_config')


SERIAL_TIMEOUT = 5


class Settings(BaseModel):
    # Throw error if unknown fields are specified.
    model_config = ConfigDict(extra="forbid")

    # The UserConfig settings that have been changed from their defaults.
    modified_settings: Dict[str, Any] = {}
    # A regex to match when checking the device navigation engine version.
    expected_engine_version: Optional[str] = None
    # A regex to match when checking the firmware version.
    expected_fw_version: Optional[str] = None
    # Whether to check if their is a completed calibration on the device.
    expect_calibration_done: Optional[bool] = None
    # Raise error if unsaved changes detected.
    expect_no_unsaved_config: Optional[bool] = None
    # Device configuration should match this p1nvm file.
    expected_config_save: Optional[str] = None
    # For Atlas, are rolling logs enabled in device controller.
    rolling_logs_enabled: Optional[bool] = None


class BalenaConfig(BaseModel):
    # Throw error if unknown fields are specified.
    model_config = ConfigDict(extra="forbid")

    # The Balena UUID for the device.
    uuid: str
    # The uuid for the release the device should be pinned to.
    pinned_release: Optional[str] = None


class DeviceConfig(BaseModel):
    # Throw error if unknown fields are specified.
    model_config = ConfigDict(extra="forbid")

    # Identifier for device.
    name: str
    # The interface for the device. Must specify either a TCP address or a
    # serial port. You cannot specify both.
    tcp_address: Optional[str] = None
    port: int = 30200

    serial_port: Optional[str] = None
    baud_rate: int = 460800

    # Balena configuration.
    balena: Optional[BalenaConfig] = None

    settings: Settings = Settings()


class TruthType(Enum):
    DEVELOP_ATLAS = 'DEVELOP_ATLAS'


class TruthConfig(BaseModel):
    # Throw error if unknown fields are specified.
    model_config = ConfigDict(extra="forbid")

    # Identifier for truth device.
    name: str

    # The kind of device the truth source is.
    type: TruthType

    # The interface for the device. Must specify a TCP address.
    tcp_address: str


class ConfigSet(BaseModel):
    '''!
    The test configuration for a set of devices.

    Device specific settings always take precedence over shared settings.

    The combined modified_settings are determined by merging the shared and device modified_settings. One area this
    can be confusing is when updating values in arrays.

    Arrays will be replaced rather then merged.
    For example if the default value was `{"arr": [0, 1]}`:
    Merging with a modification of `{"arr": [10]}` would result in `{"arr": [10]}`

    To be more explicit, using an index is also supported. Use the syntax "key_name/i" where "i" is the index of the
    array to merge changes into.
    For example if the default value was `{"arr": [{"a":0}, {"a":1}]}`:
    Merging with a modification of `{"arr/1": {"a":10}}` would result in `{"arr": [{"a":0}, {"a":10}]}`
    '''
    # Throw error if unknown fields are specified.
    model_config = ConfigDict(extra="forbid")

    # Expected configuration shared across the set of devices.
    shared: Settings = Settings()
    # The device specific expected configuration.
    devices: List[DeviceConfig]
    # Truth configuration.
    truth: Optional[TruthConfig] = None

    # Whether to expect all devices to have the same SW versions.
    expect_same_versions_on_devices: bool = False


def _check_for_lists(config: Any) -> bool:
    if isinstance(config, list):
        return True
    elif hasattr(config, 'items'):
        for _, v in config.items():
            if _check_for_lists(v):
                return True

    return False


def strip_json_comments(json_str: str) -> str:
    return re.sub(r"^\s*//.*", "", json_str, flags=re.MULTILINE)


def load_json_with_comments(config_file: str) -> Dict[str, Any]:
    with open(config_file) as fd:
        json_str = fd.read()
        # Strip out comments.
        # NOTE: This won't strip comments that are after a value.
        return json.loads(strip_json_comments(json_str))


def load_config_set(config_file: str) -> ConfigSet:
    # This is a pretty basic JSON to class conversion. Pydantic and other libraries
    # can do type safe and parsing with better error messages, but I didn't think it
    # was worth the extra dependency.
    data = load_json_with_comments(config_file)
    return load_config_set_dict(data)


def update_none_fields(target: BaseModel, source: BaseModel):
    for field in type(target).model_fields.keys():
        if getattr(target, field) is None:
            setattr(target, field, getattr(source, field))


def copy_shared_settings_to_devices(config: ConfigSet):
    for device in config.devices:
        update_none_fields(device.settings, config.shared)


def load_config_set_dict(data: Dict[str, Any]) -> ConfigSet:
    config = ConfigSet.model_validate(data)
    copy_shared_settings_to_devices(config)
    return config


def open_data_source(device_config: DeviceConfig) -> Optional[DataSource]:
    data_source = None
    if device_config.tcp_address is not None:
        logger.info('Connecting to %s using TCP address %s.' % (device_config.name, device_config.tcp_address))
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((device_config.tcp_address, device_config.port))
            data_source = SocketDataSource(s)
        except Exception as e:
            logger.error("Problem connecting to TCP address '%s': %s." % (device_config.tcp_address, str(e)))
    elif device_config.serial_port is not None:
        try:
            # Note: We intentionally use the Enhanced port here, whereas p1_runner uses Standard port. That way users can
            # issue configuration requests while the device is active and p1_runner is operating. If the user explicitly
            # sets --device-port, we'll use that port regardless of type.
            device_port = find_serial_device(port_name=device_config.serial_port, port_type=PortType.ENHANCED)
            logger.info('Connecting to %s using serial port %s.' % (device_config.name, device_port))
            serial_port = serial.Serial(port=device_port, baudrate=device_config.baud_rate, timeout=SERIAL_TIMEOUT)
            data_source = SerialDataSource(serial_port)
            data_source.start_read_thread()
        except Exception as e:
            logger.error("Problem connecting to serial port '%s': %s." % (device_config.serial_port, str(e)))
    return data_source
