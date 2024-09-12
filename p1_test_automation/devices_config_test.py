#!/usr/bin/env python3

import math
import os
import re
import struct
import sys
import time
from argparse import Namespace
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

from fusion_engine_client.messages import (ConfigResponseMessage, DataType,
                                           DeviceLeverArmConfig,
                                           EventNotificationMessage, EventType,
                                           GnssLeverArmConfig, InterfaceID,
                                           MessageRate, MessageRateResponse,
                                           NmeaMessageType,
                                           PlatformStorageDataMessage,
                                           PoseMessage, Response,
                                           VersionInfoMessage)
from fusion_engine_client.parsers.mixed_log_reader import MixedLogReader
from pydantic import BaseModel

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from bin.config_message_rate import INTERFACE_MAP, get_current_interface
from bin.config_tool import (PARAM_DEFINITION, apply_config, query_fe_version,
                             query_nmea_versions, read_config, request_export,
                             request_fault, request_import, request_reset,
                             save_config)
from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction
from p1_runner.device_interface import DeviceInterface
from p1_runner.exported_data import load_saved_data
from p1_runner.log_manager import LogManager
from p1_test_automation.devices_config import (ConfigSet, DeviceConfig,
                                               copy_shared_settings_to_devices,
                                               load_json_with_comments,
                                               open_data_source)


class TestFailure(Exception):
    pass


class IndentFilter(logging.Filter):
    """
    This is a filter which injects indentation levels into the log.
    """

    def __init__(self, depth=0):
        self.depth = depth

    def filter(self, record):
        record.extra = " " * self.depth
        return True


indent_filter = IndentFilter()


def set_logging_indent(depth):
    indent_filter.depth = depth


class InterfaceTests(BaseModel):
    # Name of device to run these test on.
    name: str
    # A list of tests to run ("version", "interface_ids", "expected_storage", "reboot",
    # "watchdog_fault", "msg_rates", "import_config", "set_config", "save_config").
    tests: List[str]
    # The interface name to expect the device to identify with.
    interface_name: Optional[str] = None


class TestConfig(BaseModel):
    config: ConfigSet
    tests: List[InterfaceTests]


@dataclass
class TestState:
    device_interface: DeviceInterface
    test_logger: LogManager
    interface_name: str
    interface_idx: Optional[InterfaceID]
    config: DeviceConfig
    fail_fast: bool
    expected_resets: int = 0
    expected_errors: int = 0
    passed: bool = True


logger = logging.getLogger('point_one.config_test')

SERIAL_TIMEOUT = 1
WATCHDOG_TIME_SEC = 5
DEFAULT_DEVICE_ID = 'config_test'
ALLOWED_RATE_ERROR_SEC = 0.2


def stop_logging(state: TestState):
    state.device_interface.data_source.stop()
    state.test_logger.stop()


def check_exit(state: TestState):
    if (state.fail_fast and not state.passed):
        stop_logging(state)
        logger.error('Test FAILURE')
        raise TestFailure('Test FAILURE')


class CalibrationStage(IntEnum):
    UNKNOWN = 0
    MOUNTING_ANGLE_INITIAL_CONVERGENCE = 1
    MOUNTING_ANGLE_FINAL_CONVERGENCE = 2
    DONE = 255


def get_calibration_stage(data: bytes) -> CalibrationStage:
    if len(data) < 1:
        return CalibrationStage.UNKNOWN
    else:
        return CalibrationStage(struct.unpack('B', data[0:1])[0])


def test_fe_version(state: TestState) -> None:
    """!
    @brief Tests that the FE version request message is working.

    In addition, it checks if the reported version matches the @ref TestConfig
    expected_version regex if specified.
    """
    logger.debug("Requesting FE version.")
    args = Namespace()
    resp = query_fe_version(state.device_interface, args)
    if not isinstance(resp, VersionInfoMessage):
        logger.error('Request failed.')
        state.passed = False
    else:
        version_str = resp.engine_version_str
        expected_version = state.config.settings.expected_engine_version
        if expected_version is not None:
            m = re.match(expected_version, version_str)
            if m is None:
                logger.error('FusionEngine version response %s did not match expected version %s.',
                             version_str, expected_version)
                state.passed = False
    check_exit(state)


def test_nmea_version(state: TestState) -> None:
    """!
    @brief Tests that the NMEA version request message is working.

    In addition, it checks if the reported version matches the @ref TestConfig
    expected_version regex if specified.
    """
    logger.debug("Requesting NMEA version.")
    args = Namespace()
    nmea_resp: Optional[List[str]] = query_nmea_versions(state.device_interface, args)
    if nmea_resp is None:
        logger.error('Request failed.')
        state.passed = False
    check_exit(state)


def test_interface_ids(state: TestState) -> None:
    """!
    @brief Checks that the interface name specified in @ref TestConfig matches
           the value reported by the device on the specified serial port.

    For example this would give an error if the uart1 config was given the
    serial port for uart2.
    """
    response_interface: Optional[InterfaceID] = get_current_interface(state.device_interface)
    if response_interface is None:
        logger.error('Request failed.')
        state.passed = False
    else:
        if response_interface != state.interface_idx:
            logger.error(
                f'Response had unexpected interface ID {response_interface}. Expected {state.interface_idx}')
            state.passed = False


def test_expected_storage(state: TestState) -> None:
    """!
    @brief Checks the calibration and UserConfig saved on the device matches the @ref TestConfig.

    This does 3 optional checks:
     - Tests if the active configuration is modified from the saved.
     - Tests if the saved configuration matches values loaded from an export file.
     - Tests if the device has a completed calibration.
    """
    active_config_path = state.test_logger.get_abs_file_path('active_config.p1nvm')

    logger.debug("Reading active UserConfig on device.")
    args = Namespace(type='user_config', format="p1nvm", export_file=active_config_path, export_source='active')
    active_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    active_config = None
    if active_storage is None:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        active_config = active_storage[0].data

    full_save_path = state.test_logger.get_abs_file_path('full_save.p1nvm')
    logger.debug("Reading all saved storage on device.")
    args = Namespace(type='all', format="p1nvm", export_file=full_save_path, export_source='saved')
    saved_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    saved_config = None
    saved_calibration = None
    if saved_storage is None:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        for storage in saved_storage:
            if storage.data_type == DataType.CALIBRATION_STATE:
                saved_calibration = storage.data
            elif storage.data_type == DataType.USER_CONFIG:
                saved_config = storage.data

        logger.debug("Checking if active config has unsaved changes.")
        if state.config.settings.expect_no_unsaved_config and active_config:
            if saved_config != active_config:
                logger.error('Active configuration differs from saved configuration.')
                state.passed = False

        if state.config.settings.expect_calibration_done:
            logger.debug("Checking if calibration is DONE.")
            if saved_calibration is not None:
                stage = get_calibration_stage(saved_calibration)
                if stage != CalibrationStage.DONE:
                    logger.error('Expected calibration to be done. Got stage %s.', stage.name)
                    state.passed = False
            else:
                logger.error('No valid saved calibration found.')
                state.passed = False

        if state.config.settings.expected_config_save is not None:
            logger.debug("Checking if saved config matched expected values.")
            expected_storage = load_saved_data(state.config.settings.expected_config_save, [DataType.USER_CONFIG])
            if len(expected_storage) > 0:
                expected_config = expected_storage[0][0].data
                if expected_config != saved_config:
                    logger.error("Saved configurations differ from expected values.")
                    state.passed = False
            else:
                logger.error("Couldn't load expected state.config %s.", state.config.settings.expected_config_save)
                state.passed = False


def test_msg_rates(state: TestState) -> None:
    """!
    @brief Checks if getting/setting message rates works as expected.

    @warning This modifies the active config. If there's a failure, the device may
             be left with these modifications until it is power cycled or reset with
             `./bin/config_tool.py save -r`.
    """
    # Disable all output on port.
    logger.debug(f"Disabling all output on {state.interface_name}.")
    args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='all',
                     message_id='all', rate='off', save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    args = Namespace(param=state.interface_name, interface_config_type='diagnostics_enabled', enabled=False, save=False)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    logger.debug(f"Checking for output.")
    state.device_interface.data_source.flush_rx()
    data = state.device_interface.data_source.read(1)

    if len(data) > 0:
        logger.error("Device sending data after disabling all output.")
        state.passed = False
        check_exit(state)

    logger.debug(f"Checking querying all message rates.")
    args = Namespace(type='active', param='current_message_rate', protocol='all', message_id='all')
    resp_read: Optional[List[MessageRateResponse]] = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(resp_read[0].rates) == 0:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        for rate_entry in resp_read[0].rates:
            if rate_entry.configured_rate != MessageRate.OFF:
                logger.error("Rates not correctly reported after disabling: %s.", rate_entry)
                state.passed = False
                check_exit(state)
                break

    # Enable pose at 1Hz.
    logger.debug(f"Checking enabling pose at 1Hz.")
    pose_id = PoseMessage.MESSAGE_TYPE
    args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='fe',
                     message_id='PoseMessage', rate='1s', save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    args = Namespace(type='active', param='current_message_rate', protocol='fe', message_id='PoseMessage')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(resp_read[0].rates) != 1:
        state.passed = False
        check_exit(state)
    elif resp_read[0].rates[0].configured_rate != MessageRate.INTERVAL_1_S:
        logger.error("Pose rate not correctly reported 1Hz: %s.", resp_read[0].rates[0])
        state.passed = False
        check_exit(state)

    if not state.device_interface.wait_for_message(pose_id):
        logger.error("Device not sending pose after enabling.")
        state.passed = False
        check_exit(state)

    start_time = time.time()
    resp = state.device_interface.wait_for_message(pose_id)
    interval = time.time() - start_time
    if resp is None or interval > 1 + ALLOWED_RATE_ERROR_SEC or interval < 1 - ALLOWED_RATE_ERROR_SEC:
        logger.error("Device not sending pose at expected 1Hz.")
        state.passed = False
        check_exit(state)

    # Enable all NMEA at 0.5Hz.
    logger.debug(f"Checking enabling GNGGA at 0.5Hz.")
    args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='nmea',
                     message_id='all', rate='500ms', save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    if not state.device_interface.wait_for_message('$GNGGA', response_timeout=10):
        logger.error("Device not sending GGA after enabling.")
        state.passed = False
        check_exit(state)

    start_time = time.time()
    resp = state.device_interface.wait_for_message('$GNGGA')
    interval = time.time() - start_time
    if resp is None or interval > 0.5 + ALLOWED_RATE_ERROR_SEC or interval < 0.5 - ALLOWED_RATE_ERROR_SEC:
        logger.error("Device not sending GGA at expected 0.5Hz.")
        state.passed = False
        check_exit(state)

    logger.debug(f"Checking querying all NMEA rates.")
    args = Namespace(type='active', param='current_message_rate', protocol='nmea', message_id='all')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(resp_read[0].rates) == 0:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        for rate_entry in resp_read[0].rates:
            nmea_without_rate_control = [NmeaMessageType.P1MSG, NmeaMessageType.PQTMTXT]
            has_expected_rate = rate_entry.message_id not in nmea_without_rate_control and rate_entry.configured_rate == MessageRate.INTERVAL_500_MS
            has_expected_disabled = rate_entry.message_id in nmea_without_rate_control and rate_entry.configured_rate == MessageRate.OFF
            if not has_expected_rate and not has_expected_disabled:
                rate_str = 'off' if rate_entry.message_id in nmea_without_rate_control else '500ms'
                logger.error("NMEA rates not correctly reported %s: %s", rate_str, rate_entry)
                state.passed = False
                check_exit(state)
                break

    # Restore saved settings.
    logger.debug(f"Restoring saved message rates.")
    args = Namespace(revert_to_saved=True, revert_to_defaults=False)
    if not save_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)


def test_set_config(state: TestState, test_save=False, use_import=False) -> None:
    """!
    @brief Checks if getting/setting configuration values works as expected.

    @warning This modifies the active config. If there's a failure, the device may
             be left with these modifications until it is power cycled or reset with
             `./bin/config_tool.py save -r`.

    @param test_save In addition to modifying the active config also test saving
           the config to flash.
           WARNING: If there's a failure, the device may be left with these
           modifications. It is recommended you backup the saved UserConfig if
           the values are important.
    @param use_import Instead of using a set_config command for restoring the
           settings, use an import command.
    """
    export_path = state.test_logger.get_abs_file_path('config_test.p1nvm')

    # Get current GNSS lever arm.
    logger.debug(f"Read lever arm.")
    args = Namespace(type='active', param='gnss')
    resp_read: Optional[List[ConfigResponseMessage]] = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        logger.error('Request failed.')
        state.passed = False
        return
    else:
        is_modified = resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED
        gnss_config = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            logger.error('Request failed.')
            state.passed = False
            return

    # Backup the current configuration for importing.
    if use_import:
        logger.debug(f"Export active config to use for restore.")
        args = Namespace(type='user_config', format="p1nvm", export_file=export_path, export_source='active')
        active_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
        if active_storage is None:
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)

    # Test modifying it.
    logger.debug(f"Modify active lever arm.")
    args = Namespace(param=f'gnss', x=gnss_config.x + 1, y=gnss_config.y, z=gnss_config.z,
                     save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    logger.debug(f"Check modification.")
    args = Namespace(type='active', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        if not (resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED):
            state.passed = False
            logger.error("Config response missing FLAG_ACTIVE_DIFFERS_FROM_SAVED.")
            check_exit(state)
        gnss_config2 = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            state.passed = False
            logger.error('Request failed.')
            check_exit(state)
        if not math.isclose(gnss_config2.x, gnss_config.x + 1, rel_tol=1e-5):
            state.passed = False
            logger.error("Config didn't match expected value after change.")
            check_exit(state)

    # Check saved value didn't change
    logger.debug(f"Check saved value unaffected.")
    args = Namespace(type='saved', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        if not (resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED):
            state.passed = False
            logger.error("Config response missing FLAG_ACTIVE_DIFFERS_FROM_SAVED.")
            check_exit(state)
        gnss_config2 = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)
        if gnss_config2.x != gnss_config.x:
            state.passed = False
            logger.error("Change modified saved value unexpectedly.")
            check_exit(state)

    # Test saving the change if called from test_save_config.
    if test_save:
        logger.debug(f"Check saving modified value.")
        args = Namespace(revert_to_saved=False, revert_to_defaults=False)
        if not save_config(state.device_interface, args):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)
        is_modified = True

        args = Namespace(type='saved', param='gnss')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)
        else:
            if resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED:
                state.passed = False
                logger.error("Config response had FLAG_ACTIVE_DIFFERS_FROM_SAVED after save.")
                check_exit(state)
            gnss_config2 = resp_read[0].config_object
            if not isinstance(gnss_config, GnssLeverArmConfig):
                logger.error('Request failed.')
                state.passed = False
                check_exit(state)
            if not math.isclose(gnss_config2.x, gnss_config.x + 1, rel_tol=1e-5):
                state.passed = False
                logger.error("Change modified saved value unexpectedly.")
                check_exit(state)

    # Test restoring it.
    if use_import:
        logger.debug(f"Check restoring value from export.")
        args = Namespace(type='user_config', preserve_unspecified=False, file=export_path,
                         dry_run=False, force=True, dont_save_config=True)
        if not request_import(state.device_interface, args):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)
    else:
        logger.debug(f"Check restoring value.")
        args = Namespace(param=f'gnss', x=gnss_config.x, y=gnss_config.y, z=gnss_config.z,
                         save=False, include_disabled=True)
        if not apply_config(state.device_interface, args):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)

    args = Namespace(type='active', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)
    else:
        if is_modified != resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED:
            state.passed = False
            logger.error("FLAG_ACTIVE_DIFFERS_FROM_SAVED did not match original value.")
            check_exit(state)
        gnss_config2 = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)
        if gnss_config2 != gnss_config:
            state.passed = False
            logger.error("Config didn't match expected value.")
            check_exit(state)

    # Restore original saved value if needed.
    if test_save:
        logger.debug(f"Save restored value.")
        args = Namespace(revert_to_saved=False, revert_to_defaults=False)
        if not save_config(state.device_interface, args):
            logger.error('Request failed.')
            state.passed = False
            check_exit(state)


def test_set_config_exhaustive(state: TestState) -> None:
    # Revert config to default.
    save_config(state.device_interface, Namespace(revert_to_saved=False, revert_to_defaults=True))

    # Create reference dictionary.
    reference_dict = {
        'gnss': {'x': 1, 'y': 2, 'z': 3},
        'device': {'x': 4, 'y': 5, 'z': 6},
        'output': {'x': 7, 'y': 8, 'z': 9},
        'vehicle_details': {'vehicle_model': 'lexus_ct200h', 'wheelbase': 2, 'front_track_width': 3, 'rear_track_width': 4},
        'wheel_config': {'wheel_sensor_type': 'ticks', 'applied_speed_type': 'front_wheels', 'steering_type': 'front',
                         'wheel_update_interval': 1, 'wheel_tick_output_interval': 2, 'steering_ratio': 15,
                         'meters_per_tick': 3, 'wheel_tick_max_value': 1000, 'wheel_ticks_signed': True,
                         'wheel_ticks_always_increase': False},
        'hardware_tick_config': {'tick_mode': 'falling_edge', 'tick_direction': 'forward_active_low', 'meters_per_tick': 2}
    }

    for param in reference_dict:
        curr_config = reference_dict[param]

        args = Namespace(param=param)
        args.save = False

        # Build config object for current param.
        definition = PARAM_DEFINITION[param]
        format = definition['format']

        for arg in curr_config:
            setattr(args, arg, curr_config[arg])

        # Get reference config object.
        arg_parse = definition['arg_parse']
        reference_config_object = arg_parse(cls=format, args=args, config_interface=state.device_interface)

        # Apply configuration
        if not apply_config(state.device_interface, args):
            logger.error(f'Request failed.')
            state.passed = False
            check_exit(state)

        # Read configuration and verify that changes were correctly applied.
        args = Namespace(type='active', param=param)
        resp_read: Optional[List[ConfigResponseMessage]] = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            logger.error('Request failed.')
            state.passed = False
            return
        else:
            curr_config_object = resp_read[0].config_object

            if not isinstance(curr_config_object, format):
                logger.error('Request failed.')
                state.passed = False
                return

        # Compare curr_config_object with reference_config_object.
        if curr_config_object == reference_config_object:
            logger.info('Check for %s passed.' % reference_config_object.__class__.__name__)
        else:
            state.passed = False
            logger.error("%s didn't match expected value." % reference_config_object.__class__.__name__)
            check_exit(state)

    # Revert to saved now to restore config to its saved state.
    save_config(state.device_interface, Namespace(revert_to_saved=True, revert_to_defaults=False))


def test_save_config(state: TestState) -> None:
    """!
    @brief Checks if saving configuration values works as expected.

    Calls test_set_config with `test_save=True`

    @copydoc test_set_config
    """
    test_set_config(state, test_save=True)


def test_import_config(state: TestState) -> None:
    """!
    @brief Checks if importing configuration values works as expected.

    Calls test_set_config with `use_import=True`

    @copydoc test_set_config
    """
    test_set_config(state, use_import=True)


def test_reboot(state: TestState) -> None:
    """!
    @brief Tests whether rebooting the processor works as expected.
    """
    if not request_reset(state.device_interface, Namespace(type=["reboot"])):
        logger.error('Request failed.')
        state.passed = False

    state.expected_resets += 1

    state.passed &= state.device_interface.wait_for_reboot()


def test_factory_reset(state: TestState) -> None:
    """!
    @brief Tests whether factory resetting the device works as expected.
    """

    # Export storage
    full_save_path = state.test_logger.get_abs_file_path('full_save.p1nvm')
    logger.info("Exporting saved storage on device.")
    args = Namespace(type='all', format="p1nvm", export_file=full_save_path, export_source='saved')
    saved_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    if saved_storage is None:
        logger.error('Storage export request failed.')
        state.passed = False
        check_exit(state)

    logger.info("Performing factory reset.")
    if not request_reset(state.device_interface, Namespace(type=["factory"])):
        logger.error('Request failed.')
        state.passed = False

    state.expected_resets += 1

    state.passed &= state.device_interface.wait_for_reboot(data_stop_timeout=10, data_restart_timeout=10)

    factory_reset_verified = True
    try:
        logger.info("Verifying factory reset parameter values.")
        args = Namespace(type='active', param='gnss')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            logger.error('Read request failed.')
            state.passed = False
            check_exit(state)

        # TODO: Make this check more complex, where it checks the entirety of the user config from platform storage.
        # Check GNSS lever arm.
        gnss_config = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            state.passed = False
            factory_reset_verified = False
            raise Exception('Failed to read GNSSLeverArmConfig')
        if not math.isclose(gnss_config.x, 0.0, rel_tol=1e-5) or not math.isclose(gnss_config.y, 0.0, rel_tol=1e-5) \
                or not math.isclose(gnss_config.z, 0.0, rel_tol=1e-5):
            state.passed = False
            factory_reset_verified = False
            raise Exception("GNSS lever arm didn't match expected value after change.")

        # Check device lever arm.
        args = Namespace(type='active', param='device')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            state.passed = False
            factory_reset_verified = False
            raise Exception('Read request failed.')

        imu_config = resp_read[0].config_object
        if not isinstance(imu_config, DeviceLeverArmConfig):
            state.passed = False
            factory_reset_verified = False
            raise Exception('Failed to read DeviceLeverArmConfig')
        if not math.isclose(imu_config.x, 0.0, rel_tol=1e-5) or not math.isclose(imu_config.y, 0.0, rel_tol=1e-5) \
                or not math.isclose(imu_config.z, 0.0, rel_tol=1e-5):
            state.passed = False
            factory_reset_verified = False
            raise Exception("Device lever arm didn't match expected value after change.")
    except Exception as e:
        logger.warning("Factory reset verification unsuccessful: %s", str(e))

    # Import storage
    logger.info("Re-importing saved storage on device.")
    args = Namespace(file=full_save_path, preserve_unspecified=False, type='all',
                     dry_run=False, force=True, dont_save_config=False)
    if not request_import(state.device_interface, args):
        logger.error('Storage import request failed.')
        state.passed = False
        check_exit(state)

    if not factory_reset_verified:
        check_exit(state)


def test_watchdog_fault(state: TestState) -> None:
    """!
    @brief Tests that the device performs a watchdog reset after a fatal fault.
    """
    # Enable the watchdog incase it's disabled.
    args = Namespace(param=f'watchdog_enabled', enabled=True, save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        logger.error('Request failed.')
        state.passed = False
        check_exit(state)

    if not request_fault(state.device_interface, Namespace(fault="fatal")):
        logger.error('Request failed.')
        state.passed = False

    time.sleep(WATCHDOG_TIME_SEC)

    state.expected_resets += 1
    state.expected_errors += 5

    state.passed &= state.device_interface.wait_for_reboot()


def logged_data_check(state: TestState) -> None:
    """!
    @brief Analyze FusionEngine in log looking for errors or sequence number jumps.
    """
    log_file = state.test_logger.get_abs_file_path('input.raw')
    reader = MixedLogReader(log_file)
    sequence_jump_count = 0
    error_count = 0
    last_sequence_num = None
    errors = []
    saw_fe = False
    for header, message in reader:
        saw_fe = True
        # Skip sequence_number 0 messages since these will be output periodically during a fatal error.
        if header.sequence_number != 0:
            if last_sequence_num is not None and header.sequence_number != last_sequence_num + 1:
                sequence_jump_count += 1
            last_sequence_num = header.sequence_number

        if isinstance(message, EventNotificationMessage) and message.event_type == EventType.LOG:
            log_level = struct.unpack('q', struct.pack('Q', message.event_flags))[0]
            if log_level <= -2:
                error_count += 1
                errors.append(message.event_description.decode('ascii'))

    if saw_fe:
        if error_count > state.expected_errors:
            logger.error("Unexpected errors during tests.\n%s", errors)
            state.passed = False

        if sequence_jump_count != state.expected_resets:
            logger.error("Expected %i jumps in sequence count, but saw %i.",
                         state.expected_resets, sequence_jump_count)
            state.passed = False


def run_tests(config: TestConfig, continue_on_failures=False) -> bool:
    module = sys.modules[__name__]

    # Copy shared settings to each interface to simplify checks.
    copy_shared_settings_to_devices(config.config)

    tests_passed = True
    for interface_tests in config.tests:
        set_logging_indent(0)
        logger.info(f'Running tests on device {interface_tests.name} ({interface_tests.interface_name}).')

        device_to_test_config: Optional[DeviceConfig] = None
        for device_config in config.config.devices:
            if device_config.name == interface_tests.name:
                device_to_test_config = device_config
                break

        if device_to_test_config is None:
            logger.error(f'No device config name matched name for tests: {interface_tests.name}')
            if continue_on_failures:
                continue
            else:
                return False

        interface_idx = None
        if interface_tests.interface_name is not None:
            interface_idx = INTERFACE_MAP.get(interface_tests.interface_name)
            if interface_idx is None:
                logger.error(f'No interface known with name: {interface_tests.interface_name}')
                if continue_on_failures:
                    continue
                else:
                    return False

        logger_manager = LogManager(interface_tests.name)
        logger_manager.start()

        data_source = open_data_source(device_to_test_config)
        if data_source is None:
            if continue_on_failures:
                continue
            else:
                return False

        data_source.rx_log = logger_manager
        interface = DeviceInterface(data_source)

        interface_name = interface_tests.interface_name if interface_tests.interface_name is not None else "current"

        state = TestState(
            device_interface=interface, test_logger=logger_manager, interface_name=interface_name,
            interface_idx=interface_idx, config=device_to_test_config, fail_fast=not continue_on_failures)

        time.sleep(0.2)
        for test_name in interface_tests.tests:
            set_logging_indent(2)
            logger.info(f"Checking {test_name}.")
            set_logging_indent(4)
            # This is the magic that checks the tests against the functions in this file.
            test_func = getattr(module, 'test_' + test_name, None)
            try:
                if test_func is None:
                    logger.error('Invalid test %s.', test_name)
                else:
                    test_func(state)
                check_exit(state)
            except TestFailure:
                return False
            # Make sure there's some time between each test.
            time.sleep(0.2)
            if state.passed:
                logger.info(f"Check passed.")

        set_logging_indent(2)
        stop_logging(state)
        logger.info("Checking captured log.")
        logged_data_check(state)
        if state.passed:
            logger.info(f"Log data passed.")
        # Don't exit on a log check failure since this is likely caused by
        # spurious error messages.
        tests_passed &= state.passed

    set_logging_indent(0)
    if tests_passed:
        logger.info('Test SUCCESS')
        return True
    else:
        logger.error('Test FAILURE')
        return False


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]...' % execute_command,
        description='Run configuration interface validation tests.',
        epilog='''\
EXAMPLE USAGE

Run the default test sets on a device connected using automatic serial port
detection:
    %(command)s

Run a test set specified in config_examples/config_test.json:
    %(command)s -t config_examples/config_test.json

JSON FORMAT

{
    "config": See ConfigSet class in p1_test_automation/devices_config_test.py
    "tests": [
        {
        "name": Name of device in config to run these test on.
        "tests": subset of tests to run ["fe_version", "interface_ids", "expected_storage", "reboot", "watchdog_fault", "msg_rates", "set_config", "import_config", "save_config"]
        "interface_name": [Optional] the interface name to expect the device to identify with.
        },
        ...
    ]
}

NOTES

Device or test failures may leave the device with a modified configuration.
Backing up the configuration and pointing to the backup with the
`expected_config_save` flag is recommended to validate config is set to expected
values.

TEST SIDE EFFECTS

 - reboot and watchdog_fault: Cause the device to reboot.
 - msg_rates and set_config and import_config: Will temporarily modify the active configuration.
 - save_config: Will temporarily modify the saved and active configuration and set it back to the saved values.
''' % {'command': execute_command})

    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")
    parser.add_argument(
        '-c', '--continue-on-failures', action=ExtendedBooleanAction,
        help="Don't stop running tests on the first failure.")
    parser.add_argument(
        '-t', '--test-configuration', required=True,
        help="A JSON file with the configuration for the tests to run. See the TestConfig class for the structure or config_examples/config_test.json.")
    args = parser.parse_args()

    if args.verbose == 0:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO,
                            format='[%(filename)s:%(lineno)d] %(extra)s%(message)s', stream=sys.stdout)
    else:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.INFO,
                            format='[%(filename)s:%(lineno)-4d] %(asctime)s - %(levelname)-8s - %(extra)s%(message)s',
                            stream=sys.stdout)

    if args.verbose < 2:
        logging.getLogger('point_one.config_tool').setLevel(logging.WARNING)
        logging.getLogger('point_one.exported_data').setLevel(logging.WARNING)
        logging.getLogger('point_one.log_manager').setLevel(logging.WARNING)
    elif args.verbose == 2:
        pass
    elif args.verbose == 3:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.DEBUG)
        logging.getLogger('point_one.device_interface').setLevel(logging.DEBUG)
    else:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.TRACE)
        logging.getLogger('point_one.device_interface').setLevel(logging.TRACE)

    for handler in logging.Logger.root.handlers:
        handler.addFilter(indent_filter)

    data = load_json_with_comments(args.test_configuration)
    config = TestConfig.model_validate(data)

    if not run_tests(config, args.continue_on_failures):
        sys.exit(1)


if __name__ == "__main__":
    main()
