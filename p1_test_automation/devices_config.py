import copy
import json
import logging
import re
import socket
from enum import Enum, auto
from typing import Any, Dict, List, NamedTuple, Optional

import serial

# isort: split
from p1_runner.data_source import (DataSource, SerialDataSource,
                                   SocketDataSource)
from p1_runner.find_serial_device import PortType, find_serial_device

logger = logging.getLogger('point_one.test_automation.device_config')


SERIAL_TIMEOUT = 5


class SharedConfig(NamedTuple):
    # The UserConfig settings that have been changed from their defaults.
    modified_settings: Dict[str, Any] = {}
    # A regex to match when checking the device navigation engine version.
    expected_engine_version: Optional[str] = None
    # A regex to match when checking the firmware version.
    expected_fw_version: Optional[str] = None
    # Whether to check if their is a completed calibration on the device.
    expect_calibration_done: bool = False
    # For Atlas, are rolling logs enabled in device controller.
    rolling_logs_enabled: Optional[bool] = None
    # Whether to expect all devices to have the same SW versions.
    expect_same_versions_on_devices: bool = False


class BalenaConfig(NamedTuple):
    # The Balena UUID for the device.
    uuid: str

    # The uuid for the release the device should be pinned to.
    pinned_release: Optional[str] = None


class DeviceConfig(NamedTuple):
    # Identifier for device.
    name: str

    # The UserConfig settings that have been changed from their defaults.
    modified_settings: Dict[str, Any]

    # The interface for the device. Must specify either a TCP address or a
    # serial port. You cannot specify both.
    tcp_address: Optional[str] = None
    port: int = 30200

    serial_port: Optional[str] = None
    baud_rate: int = 460800

    # For Atlas, are rolling logs enabled in device controller.
    rolling_logs_enabled: Optional[bool] = None

    # A regex to match when checking the device navigation engine version.
    expected_engine_version: Optional[str] = None
    # A regex to match when checking the firmware version.
    expected_fw_version: Optional[str] = None
    # Whether to check if their is a completed calibration on the device.
    expect_calibration_done: bool = False

    # Balena configuration.
    balena: Optional[BalenaConfig] = None


class TruthType(Enum):
    DEVELOP_ATLAS = auto()


class TruthConfig(NamedTuple):
    # Identifier for truth device.
    name: str

    # The kind of device the truth source is.
    type: TruthType

    # The interface for the device. Must specify a TCP address.
    tcp_address: str


class ConfigSet(NamedTuple):
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

    # Expected configuration shared across the set of devices.
    shared: SharedConfig
    # The device specific expected configuration.
    devices: List[DeviceConfig]
    # Truth configuration.
    truth: Optional[TruthConfig]


def _check_for_lists(config: Any) -> bool:
    if isinstance(config, list):
        return True
    elif hasattr(config, 'items'):
        for _, v in config.items():
            if _check_for_lists(v):
                return True

    return False


def load_json_with_comments(config_file: str) -> Dict[str, Any]:
    with open(config_file) as fd:
        json_str = fd.read()
        # Strip out comments.
        # NOTE: This won't strip comments that are after a value.
        return json.loads(re.sub(r"^\s*//.*", "", json_str, flags=re.MULTILINE))


def load_config_set(config_file: str) -> ConfigSet:
    # This is a pretty basic JSON to class conversion. Pydantic and other libraries
    # can do type safe and parsing with better error messages, but I didn't think it
    # was worth the extra dependency.
    data = load_json_with_comments(config_file)
    return load_config_set_dict(data)


def load_config_set_dict(data: Dict[str, Any]) -> ConfigSet:
    # Make a copy to avoid modifying original data.
    data = copy.deepcopy(data)
    shared_data = data.get('shared', {})
    shared = SharedConfig(**shared_data)

    truth = TruthConfig(**data['truth_source']) if 'truth_source' in data else None
    if 'truth_source' in data:
        truth_data = data['truth_source']
        # Convert type to enum.
        truth_data['type'] = TruthType[truth_data['type']]
        truth = TruthConfig(**truth_data)
    else:
        truth = None

    has_array_settings = _check_for_lists(shared.modified_settings)

    devices = []
    for device in data['devices']:
        if 'tcp_address' in device and 'serial_port' in device:
            raise KeyError("Can't specify both tcp_address and serial_port")
        elif 'tcp_address' not in device and 'serial_port' not in device:
            raise KeyError("Must specify either tcp_address and serial_port")

        if 'balena' in device:
            device['balena'] = BalenaConfig(**device['balena'])

        # Fall back to shared value if device value not set.
        if shared.expect_calibration_done and 'expect_calibration_done' not in device:
            device['expect_calibration_done'] = True

        if shared.rolling_logs_enabled and 'rolling_logs_enabled' not in device:
            device['rolling_logs_enabled'] = True

        devices.append(DeviceConfig(**device))

        has_array_settings |= _check_for_lists(devices[-1].modified_settings)

    if has_array_settings:
        logger.info(
            '''\
modified_settings contain arrays.
These replace, rather then update the existing settings. This may lead to unexpected changes.
Consider specifying an index instead. See device_config.ConfigSet for more details.'''
        )

    return ConfigSet(shared=shared, devices=devices, truth=truth)


def open_data_source(device_config: DeviceConfig) -> Optional[DataSource]:
    data_source = None
    if device_config.tcp_address is not None:
        logger.info('Connecting to %s using TCP address %s.' % (device_config.name, device_config.tcp_address))
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((device_config.tcp_address, device_config.port))
            data_source = SocketDataSource(s)
        except Exception as e:
            logger.error("Problem connecting to TCP address '%s': %s." % (device_config.tcp_address, str(e)))
    elif device_config.serial_port is not None:
        # Note: We intentionally use the Enhanced port here, whereas p1_runner uses Standard port. That way users can
        # issue configuration requests while the device is active and p1_runner is operating. If the user explicitly
        # sets --device-port, we'll use that port regardless of type.
        device_port = find_serial_device(port_name=device_config.serial_port, port_type=PortType.ENHANCED)
        logger.info('Connecting to %s using serial port %s.' % (device_config.name, device_port))
        serial_port = serial.Serial(port=device_port, baudrate=device_config.baud_rate, timeout=SERIAL_TIMEOUT)
        data_source = SerialDataSource(serial_port)
        data_source.start_read_thread()
    return data_source
