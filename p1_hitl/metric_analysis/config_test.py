#!/usr/bin/env python3

import inspect
import math
import os
import re
import struct
import sys
import time
import traceback
from argparse import Namespace
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import List, Optional

from fusion_engine_client.messages import (ConfigResponseMessage, DataType,
                                           DeviceLeverArmConfig,
                                           EventNotificationMessage,
                                           GnssLeverArmConfig, InterfaceID,
                                           MessageHeader, MessageRate,
                                           MessageRateResponse,
                                           NmeaMessageType,
                                           PlatformStorageDataMessage,
                                           PoseMessage, Response,
                                           VersionInfoMessage)
from fusion_engine_client.parsers import fast_indexer
from pydantic import BaseModel

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from bin.config_message_rate import INTERFACE_MAP, get_current_interface
from bin.config_tool import (PARAM_DEFINITION, apply_config, query_fe_version,
                             query_nmea_versions, read_config, request_export,
                             request_fault, request_import, request_reset,
                             save_config)
from p1_hitl.defs import DeviceType, HitlEnvArgs, TestType
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CodeLocation,
                                             FatalMetricException,
                                             MetricController)
from p1_runner import trace as logging
from p1_runner.device_interface import DeviceInterface
from p1_runner.exported_data import load_saved_data
from p1_runner.log_manager import LogManager
from p1_test_automation.devices_config import (ConfigSet, DeviceConfig,
                                               copy_shared_settings_to_devices,
                                               open_data_source)

metric_run_configuration_check = AlwaysTrueMetric(
    'run_configuration_check',
    'Run the configuration set appropriate for the particular device. These are registered at runtime.',
    is_required=True,
)


def configure_metrics(env_args: HitlEnvArgs):
    # When running configuration test, disable all other metrics.
    if env_args.get_selected_test_type() == TestType.CONFIGURATION:
        for metric in MetricController._metrics.values():
            if metric is not metric_run_configuration_check:
                metric.is_disabled = True
    else:
        metric_run_configuration_check.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


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
    expected_resets: int = 0
    expected_errors: int = 0


logger = logging.getLogger('point_one.config_test')

SERIAL_TIMEOUT = 1
WATCHDOG_TIME_SEC = 5
DEFAULT_DEVICE_ID = 'config_test'
ALLOWED_RATE_ERROR_SEC = 0.2


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


def ConfigCheck(interface_name: str, metric_name: str, description: str, passed: bool, context: Optional[str] = None):
    name = metric_name + '_' + interface_name
    # If this is the first reference to this metric, create it.
    if name not in MetricController._metrics:
        metric = AlwaysTrueMetric(name, description, is_fatal=True)
    else:
        metric: AlwaysTrueMetric = MetricController._metrics[name]  # type: ignore

    # If this test already failed, don't do additional checks. Mostly relevant for cleanup code.
    if metric.failure_time is None:
        # Update code_location to the caller of this function.
        frame = inspect.stack()[1]
        metric.code_location = CodeLocation(Path(frame.filename), frame.lineno)
        metric.check(passed, context)


def test_fe_version(state: TestState) -> None:
    """!
    @brief Tests that the FE version request message is working.

    In addition, it checks if the reported version matches the @ref TestConfig
    expected_version regex if specified.
    """
    logger.debug("Requesting FE version.")
    args = Namespace()
    resp = query_fe_version(state.device_interface, args)
    metric_name = 'fe_version_check'
    metric_description = 'Send a version FE command and check the response.'
    ConfigCheck(state.interface_name, metric_name, metric_description, True)
    if not isinstance(resp, VersionInfoMessage):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Request failed.')
    else:
        version_str = resp.engine_version_str
        expected_version = state.config.settings.expected_engine_version
        if expected_version is not None:
            m = re.match(expected_version, version_str)
            if m is None:
                ConfigCheck(state.interface_name,
                            metric_name,
                            metric_description,
                            False,
                            f'Response {version_str} did not match expected version {expected_version}.')


def test_nmea_version(state: TestState) -> None:
    """!
    @brief Tests that the NMEA version request message is working.
    """
    logger.debug("Requesting NMEA version.")
    metric_name = 'nmea_version_check'
    metric_description = 'Send a version FE command and check for a response.'
    args = Namespace()
    nmea_resp: Optional[List[str]] = query_nmea_versions(state.device_interface, args)
    ConfigCheck(state.interface_name, metric_name, metric_description, nmea_resp is not None, 'Request failed.')


def test_interface_ids(state: TestState) -> None:
    """!
    @brief Checks that the interface name specified in @ref TestConfig matches
           the value reported by the device on the specified serial port.

    For example this would give an error if the uart1 config was given the
    serial port for uart2.
    """
    response_interface: Optional[InterfaceID] = get_current_interface(state.device_interface)
    metric_name = 'interface_id'
    metric_description = 'Checks that the interface ID reported by the device matches what was requested.'
    if response_interface is None:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Request failed.')
    else:
        ConfigCheck(state.interface_name, metric_name, metric_description, response_interface == state.interface_idx,
                    f'Response had unexpected interface ID {response_interface}. Expected {state.interface_idx}.')


def test_expected_storage(state: TestState) -> None:
    """!
    @brief Checks the calibration and UserConfig saved on the device matches the @ref TestConfig.

    This does 3 optional checks:
     - Tests if the active configuration is modified from the saved.
     - Tests if the saved configuration matches values loaded from an export file.
     - Tests if the device has a completed calibration.
    """
    active_config_path = state.test_logger.get_abs_file_path('active_config.p1nvm')

    metric_name = 'expected_storage'
    metric_description = 'Read and validate device storage.'
    ConfigCheck(state.interface_name, metric_name, metric_description, True)

    logger.debug("Reading active UserConfig on device.")
    args = Namespace(type='user_config', format="p1nvm", export_file=active_config_path, export_source='active')
    active_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    active_config = None
    if active_storage is None:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Request failed.')
    else:
        active_config = active_storage[0].data

    full_save_path = state.test_logger.get_abs_file_path('full_save.p1nvm')
    logger.debug("Reading all saved storage on device.")
    args = Namespace(type='all', format="p1nvm", export_file=full_save_path, export_source='saved')
    saved_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    saved_config = None
    saved_calibration = None
    if saved_storage is None:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Request failed.')
    else:
        for storage in saved_storage:
            if storage.data_type == DataType.CALIBRATION_STATE:
                saved_calibration = storage.data
            elif storage.data_type == DataType.USER_CONFIG:
                saved_config = storage.data

        logger.debug("Checking if active config has unsaved changes.")
        if state.config.settings.expect_no_unsaved_config and active_config:
            ConfigCheck(state.interface_name, metric_name, metric_description, saved_config == active_config,
                        'Active configuration differs from saved configuration.')

        if state.config.settings.expect_calibration_done:
            logger.debug("Checking if calibration is DONE.")
            if saved_calibration is not None:
                stage = get_calibration_stage(saved_calibration)
                ConfigCheck(state.interface_name, metric_name, metric_description, stage == CalibrationStage.DONE,
                            f'Expected calibration to be done. Got stage {stage.name}.')
            else:
                ConfigCheck(
                    state.interface_name,
                    metric_name,
                    metric_description,
                    False,
                    'No valid saved calibration found.')

        if state.config.settings.expected_config_save is not None:
            logger.debug("Checking if saved config matched expected values.")
            expected_storage = load_saved_data(state.config.settings.expected_config_save, [DataType.USER_CONFIG])
            if len(expected_storage) > 0:
                expected_config = expected_storage[0][0].data
                ConfigCheck(state.interface_name,
                            metric_name,
                            metric_description,
                            expected_config == saved_config,
                            "Saved configurations differ from expected values.")
            else:
                ConfigCheck(state.interface_name,
                            metric_name,
                            metric_description,
                            False,
                            f"Couldn't load expected state.config {state.config.settings.expected_config_save}.")


def test_msg_rates(state: TestState) -> None:
    """!
    @brief Checks if getting/setting message rates works as expected.

    @warning This modifies the active config. If there's a failure, the device may
             be left with these modifications until it is power cycled or reset with
             `./bin/config_tool.py save -r`.
    """
    metric_name = 'msg_rates_config'
    metric_description = 'Checks if getting/setting message rates works as expected.'
    ConfigCheck(state.interface_name, metric_name, metric_description, True)
    try:
        # Disable all output on port.
        logger.debug(f"Disabling all output on {state.interface_name}.")
        args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='all',
                         message_id='all', rate='off', save=False, include_disabled=True)
        if not apply_config(state.device_interface, args):
            ConfigCheck(
                state.interface_name,
                metric_name,
                metric_description,
                False,
                'Disable all output request failed.')

        args = Namespace(
            param=state.interface_name,
            interface_config_type='diagnostics_enabled',
            enabled=False,
            save=False)
        if not apply_config(state.device_interface, args):
            ConfigCheck(
                state.interface_name,
                metric_name,
                metric_description,
                False,
                'diagnostics_enabled request failed.')

        logger.debug(f"Checking for output.")
        state.device_interface.data_source.flush_rx()
        data = state.device_interface.data_source.read(1)

        ConfigCheck(state.interface_name, metric_name, metric_description, len(data) == 0,
                    "Device still sending data after disabling all output.")

        logger.debug(f"Checking querying all message rates.")
        args = Namespace(type='active', param='current_message_rate', protocol='all', message_id='all')
        resp_read: Optional[List[MessageRateResponse]] = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(
                resp_read[0].rates) == 0:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Message rate query failed.')
        else:
            for rate_entry in resp_read[0].rates:
                ConfigCheck(state.interface_name, metric_name, metric_description, rate_entry.configured_rate == MessageRate.OFF,
                            f"Rates not correctly reported after disabling: {rate_entry}.")

        # Enable pose at 1Hz.
        logger.debug(f"Checking enabling pose at 1Hz.")
        args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='fe',
                         message_id='PoseMessage', rate='1s', save=False, include_disabled=True)
        if not apply_config(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Setting pose rate failed.')

        args = Namespace(type='active', param='current_message_rate', protocol='fe', message_id='PoseMessage')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(
                resp_read[0].rates) != 1:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'FE message rate query failed.')
        elif resp_read[0].rates[0].configured_rate != MessageRate.INTERVAL_1_S:
            ConfigCheck(state.interface_name,
                        metric_name,
                        metric_description,
                        False,
                        f"Pose rate not correctly reported 1Hz: {resp_read[0].rates[0]}.")

        if not state.device_interface.wait_for_message(PoseMessage.MESSAGE_TYPE):
            ConfigCheck(state.interface_name, metric_name, metric_description,
                        False, "Device not sending pose after enabling.")

        start_time = time.time()
        resp = state.device_interface.wait_for_message(PoseMessage.MESSAGE_TYPE)
        interval = time.time() - start_time
        if resp is None or interval > 1 + ALLOWED_RATE_ERROR_SEC or interval < 1 - ALLOWED_RATE_ERROR_SEC:
            ConfigCheck(state.interface_name, metric_name, metric_description,
                        False, "Device not sending pose at expected 1Hz.")

        # Enable all NMEA at 0.5Hz.
        logger.debug(f"Checking enabling GNGGA at 0.5Hz.")
        args = Namespace(param=state.interface_name, interface_config_type='message_rate', protocol='nmea',
                         message_id='all', rate='500ms', save=False, include_disabled=True)
        if not apply_config(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Setting GNGGA rate failed.')

        if not state.device_interface.wait_for_message('$GNGGA', response_timeout=10):
            ConfigCheck(state.interface_name, metric_name, metric_description,
                        False, "Device not sending GGA after enabling.")

        start_time = time.time()
        resp = state.device_interface.wait_for_message('$GNGGA')
        interval = time.time() - start_time
        if resp is None or interval > 0.5 + ALLOWED_RATE_ERROR_SEC or interval < 0.5 - ALLOWED_RATE_ERROR_SEC:
            ConfigCheck(state.interface_name, metric_name, metric_description,
                        False, "Device not sending GGA at expected 0.5Hz.")

        logger.debug(f"Checking querying all NMEA rates.")
        args = Namespace(type='active', param='current_message_rate', protocol='nmea', message_id='all')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK or len(
                resp_read[0].rates) == 0:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'NMEA message rate query failed.')
        else:
            for rate_entry in resp_read[0].rates:
                nmea_without_rate_control = [NmeaMessageType.P1MSG, NmeaMessageType.PQTMTXT]
                has_expected_rate = rate_entry.message_id not in nmea_without_rate_control and \
                    rate_entry.configured_rate == MessageRate.INTERVAL_500_MS
                has_expected_disabled = rate_entry.message_id in nmea_without_rate_control and \
                    rate_entry.configured_rate == MessageRate.OFF
                if not has_expected_rate and not has_expected_disabled:
                    rate_str = 'off' if rate_entry.message_id in nmea_without_rate_control else '500ms'
                    ConfigCheck(state.interface_name, metric_name, metric_description, False,
                                f"NMEA rates not correctly reported {rate_str}: {rate_entry}")
    finally:
        # Restore saved settings.
        logger.debug("Restoring saved message rates.")
        args = Namespace(revert_to_saved=True, revert_to_defaults=False)
        if not save_config(state.device_interface, args):
            ConfigCheck(
                state.interface_name,
                metric_name,
                metric_description,
                False,
                "Restoring saved message rates failed.")


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

    metric_name = 'set_config'
    if test_save:
        metric_name += '_saved'
    if use_import:
        metric_name += '_imports'
    metric_description = 'Checks if changing config works as expected.'
    ConfigCheck(state.interface_name, metric_name, metric_description, True)

    # Get current GNSS lever arm.
    logger.debug(f"Read lever arm.")
    args = Namespace(type='active', param='gnss')
    resp_read: Optional[List[ConfigResponseMessage]] = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')
    else:
        gnss_config = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')

    # Backup the current configuration for importing.
    if use_import:
        logger.debug(f"Export active config to use for restore.")
        args = Namespace(type='user_config', format="p1nvm", export_file=export_path, export_source='active')
        active_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
        ConfigCheck(
            state.interface_name,
            metric_name,
            metric_description,
            active_storage is not None,
            'Export active config failed.')

    # Test modifying it.
    logger.debug(f"Modify active lever arm.")
    args = Namespace(param=f'gnss', x=gnss_config.x + 1, y=gnss_config.y, z=gnss_config.z,
                     save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Set lever arm failed.')

    logger.debug(f"Check modification.")
    args = Namespace(type='active', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')
    else:
        if not isinstance(gnss_config, GnssLeverArmConfig):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')
        if not (resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED):
            ConfigCheck(state.interface_name, metric_name, metric_description, False,
                        "Config response missing FLAG_ACTIVE_DIFFERS_FROM_SAVED.")
        gnss_config2 = resp_read[0].config_object
        if not math.isclose(gnss_config2.x, gnss_config.x + 1, rel_tol=1e-5):
            ConfigCheck(state.interface_name,
                        metric_name,
                        metric_description,
                        False,
                        f"Config didn't match expected value after change. [expected:{gnss_config.x + 1} got:{gnss_config2.x}]")

    # Check saved value didn't change
    logger.debug(f"Check saved value unaffected.")
    args = Namespace(type='saved', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read saved lever arm failed.')
    else:
        if not isinstance(gnss_config, GnssLeverArmConfig):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read saved lever arm failed.')
        if not (resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED):
            ConfigCheck(state.interface_name, metric_name, metric_description, False,
                        "Config response missing FLAG_ACTIVE_DIFFERS_FROM_SAVED.")
        gnss_config2 = resp_read[0].config_object
        ConfigCheck(state.interface_name, metric_name, metric_description, gnss_config2.x == gnss_config.x,
                    f"Change modified saved value unexpectedly. [expected:{gnss_config.x} got:{gnss_config2.x}]")

    # Test saving the change if called from test_save_config.
    if test_save:
        logger.debug(f"Check saving modified value.")
        args = Namespace(revert_to_saved=False, revert_to_defaults=False)
        if not save_config(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Save request failed.')

        args = Namespace(type='saved', param='gnss')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read saved lever arm failed.')
        else:
            if not isinstance(gnss_config, GnssLeverArmConfig):
                ConfigCheck(
                    state.interface_name,
                    metric_name,
                    metric_description,
                    False,
                    'Read saved lever arm failed.')
            gnss_config2 = resp_read[0].config_object
            if not math.isclose(gnss_config2.x, gnss_config.x + 1, rel_tol=1e-5):
                ConfigCheck(state.interface_name,
                            metric_name,
                            metric_description,
                            False,
                            f"Saved config didn't match expected value after change."
                            f"[expected:{gnss_config.x + 1} got:{gnss_config2.x}]")
            if resp_read[0].flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED:
                ConfigCheck(state.interface_name,
                            metric_name,
                            metric_description,
                            False,
                            "Config response set FLAG_ACTIVE_DIFFERS_FROM_SAVED unexpectedly.")

    # Test restoring it.
    if use_import:
        logger.debug(f"Check restoring value from export.")
        args = Namespace(type='user_config', preserve_unspecified=False, file=export_path,
                         dry_run=False, force=True, dont_save_config=True)
        if not request_import(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Import failed.')
    else:
        logger.debug(f"Check restoring value.")
        args = Namespace(param=f'gnss', x=gnss_config.x, y=gnss_config.y, z=gnss_config.z,
                         save=False, include_disabled=True)
        if not apply_config(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Set lever arm failed.')

    args = Namespace(type='active', param='gnss')
    resp_read = read_config(state.device_interface, args)
    if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')
    else:
        gnss_config2 = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read lever arm failed.')
        if gnss_config2 != gnss_config:
            ConfigCheck(state.interface_name,
                        metric_name,
                        metric_description,
                        False,
                        f"Restored config didn't match expected value. [expected:{gnss_config.x} got:{gnss_config2.x}]")

    # Restore original saved value if needed.
    if test_save:
        logger.debug(f"Save restored value.")
        args = Namespace(revert_to_saved=False, revert_to_defaults=False)
        if not save_config(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Save request failed.')


def test_set_config_exhaustive(state: TestState) -> None:
    metric_name = 'set_config_exhaustive'
    metric_description = 'Checks changing several different config settings.'
    ConfigCheck(state.interface_name, metric_name, metric_description, True)

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

    for param, curr_config in reference_dict.items():
        logger.info(f'Trying to update {param}.')
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
            ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Setting {param} failed.")

        # Read configuration and verify that changes were correctly applied.
        args = Namespace(type='active', param=param)
        resp_read: Optional[List[ConfigResponseMessage]] = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Reading {param} failed.")
        else:
            curr_config_object = resp_read[0].config_object

            if not isinstance(curr_config_object, format):
                ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Reading {param} failed.")
                return

        ConfigCheck(state.interface_name,
                    metric_name,
                    metric_description,
                    curr_config_object == reference_config_object,
                    f"{param} didn't match expected value after change.")

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
    metric_name = 'reboot'
    metric_description = 'Tests whether rebooting the processor works as expected.'
    if not request_reset(state.device_interface, Namespace(type=["reboot"])):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Reboot request failed.")

    state.expected_resets += 1

    ConfigCheck(state.interface_name,
                metric_name,
                metric_description,
                state.device_interface.wait_for_reboot(),
                f"Timed out waiting for reboot.")


def test_factory_reset(state: TestState) -> None:
    """!
    @brief Tests whether factory resetting the device works as expected.
    """
    metric_name = 'factory_reset'
    metric_description = 'Tests whether factory resetting the device works as expected.'

    # Export storage
    full_save_path = state.test_logger.get_abs_file_path('full_save.p1nvm')
    logger.info("Exporting saved storage on device.")
    args = Namespace(type='all', format="p1nvm", export_file=full_save_path, export_source='saved')
    saved_storage: Optional[List[PlatformStorageDataMessage]] = request_export(state.device_interface, args)
    if saved_storage is None:
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Storage export request failed.')

    logger.info("Performing factory reset.")
    if not request_reset(state.device_interface, Namespace(type=["factory"])):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Factory reset request failed.')

    state.expected_resets += 1

    rebooted_successfully = state.device_interface.wait_for_reboot(data_stop_timeout=10, data_restart_timeout=10)
    ConfigCheck(state.interface_name, metric_name, metric_description, rebooted_successfully, 'Reboot timed out.')

    try:
        logger.info("Verifying factory reset parameter values.")
        args = Namespace(type='active', param='gnss')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read request failed.')
            return

        # TODO: Make this check more complex, where it checks the entirety of the user config from platform storage.
        # Check GNSS lever arm.
        gnss_config = resp_read[0].config_object
        if not isinstance(gnss_config, GnssLeverArmConfig):
            ConfigCheck(
                state.interface_name,
                metric_name,
                metric_description,
                False,
                'Failed to read GNSSLeverArmConfig')
        if not math.isclose(gnss_config.x, 0.0, rel_tol=1e-5) or not math.isclose(gnss_config.y, 0.0, rel_tol=1e-5) \
                or not math.isclose(gnss_config.z, 0.0, rel_tol=1e-5):
            ConfigCheck(state.interface_name, metric_name, metric_description, False,
                        "GNSS lever arm didn't match expected value after change.")

        # Check device lever arm.
        args = Namespace(type='active', param='device')
        resp_read = read_config(state.device_interface, args)
        if resp_read is None or len(resp_read) != 1 or resp_read[0].response != Response.OK:
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Read request failed.')
            return

        imu_config = resp_read[0].config_object
        if not isinstance(imu_config, DeviceLeverArmConfig):
            ConfigCheck(
                state.interface_name,
                metric_name,
                metric_description,
                False,
                'Failed to read DeviceLeverArmConfig')
        if not math.isclose(imu_config.x, 0.0, rel_tol=1e-5) or not math.isclose(imu_config.y, 0.0, rel_tol=1e-5) \
                or not math.isclose(imu_config.z, 0.0, rel_tol=1e-5):
            ConfigCheck(state.interface_name, metric_name, metric_description, False,
                        "Device lever arm didn't match expected value after change.")
    finally:
        # Import storage
        logger.info("Re-importing saved storage on device.")
        args = Namespace(file=full_save_path, preserve_unspecified=False, type='all',
                         dry_run=False, force=True, dont_save_config=False)
        if not request_import(state.device_interface, args):
            ConfigCheck(state.interface_name, metric_name, metric_description, False, 'Storage import request failed.')


def test_watchdog_fault(state: TestState) -> None:
    """!
    @brief Tests that the device performs a watchdog reset after a fatal fault.
    """
    metric_name = 'watchdog_fault'
    metric_description = 'Tests that the device performs a watchdog reset after a fatal fault.'
    # Enable the watchdog incase it's disabled.
    args = Namespace(param=f'watchdog_enabled', enabled=True, save=False, include_disabled=True)
    if not apply_config(state.device_interface, args):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Enable watchdog failed.")

    if not request_fault(state.device_interface, Namespace(fault="fatal")):
        ConfigCheck(state.interface_name, metric_name, metric_description, False, f"Fatal fault failed.")

    time.sleep(WATCHDOG_TIME_SEC)

    state.expected_resets += 1
    state.expected_errors += 5

    ConfigCheck(state.interface_name,
                metric_name,
                metric_description,
                state.device_interface.wait_for_reboot(),
                f"Timed out waiting for watchdog reboot.")


def get_log_size(log_manager: LogManager) -> int:
    log_file = log_manager.get_abs_file_path('input.raw')
    if os.path.exists(log_file):
        return os.stat(log_file).st_size
    else:
        return 0


def logged_data_check(state: TestState, log_start_offset: int) -> None:
    """!
    @brief Analyze FusionEngine in log looking for errors or sequence number jumps.
    """
    log_file = state.test_logger.get_abs_file_path('input.raw')
    if not os.path.exists(log_file):
        return

    sequence_jump_count = 0
    error_count = 0
    last_sequence_num = None
    errors = []
    saw_fe = False
    file_index = fast_indexer.fast_generate_index(log_file)
    HEADER_SIZE = MessageHeader.calcsize()
    event_notification = EventNotificationMessage()
    with open(log_file, 'rb') as in_fd:
        header = MessageHeader()
        for offset in file_index.offset:
            if offset < log_start_offset:
                continue
            in_fd.seek(offset)
            data = in_fd.read(HEADER_SIZE)
            header.unpack(data)

            saw_fe = True
            # Skip sequence_number 0 messages since these will be output periodically during a fatal error.
            if header.sequence_number != 0:
                if last_sequence_num is not None and header.sequence_number != last_sequence_num + 1:
                    sequence_jump_count += 1
                last_sequence_num = header.sequence_number

            if header.message_type == EventNotificationMessage.MESSAGE_TYPE:
                data = in_fd.read(header.payload_size_bytes)
                event_notification.unpack(data)
                log_level = struct.unpack('q', struct.pack('Q', event_notification.event_flags))[0]
                if log_level <= -2:
                    error_count += 1
                    errors.append(event_notification.event_description.decode('ascii'))
    if saw_fe:
        ConfigCheck(state.interface_name,
                    'error_msgs_during_test',
                    "Check the device doesn't send any error notifications",
                    error_count < state.expected_errors,
                    f"{error_count} during tests.")
        ConfigCheck(state.interface_name,
                    'error_msgs_during_test',
                    "Check the device doesn't send any error notifications",
                    sequence_jump_count == state.expected_resets,
                    f"Expected {state.expected_resets} jumps in sequence count, but saw {sequence_jump_count}.")


def run_tests(env_args: HitlEnvArgs, device_config: DeviceConfig, logger_manager: LogManager) -> bool:
    module = sys.modules[__name__]

    if env_args.HITL_BUILD_TYPE.is_lg69t():
        device_config1 = device_config.model_copy()
        device_config1.name += '_uart1'
        device_config1.serial_port = env_args.JENKINS_UART1
        device_config2 = device_config.model_copy()
        device_config2.name += '_uart2'
        device_config2.serial_port = env_args.JENKINS_UART2

        test_config = TestConfig(
            config=ConfigSet(
                devices=[device_config1, device_config2]
            ),
            tests=[
                InterfaceTests(
                    name=device_config1.name,
                    interface_name='uart1',
                    tests=[
                        "fe_version",
                        "nmea_version",
                        "interface_ids",
                        "expected_storage",
                        "msg_rates",
                        "set_config",
                        "import_config"]
                ),
                InterfaceTests(
                    name=device_config2.name,
                    interface_name='uart2',
                    tests=["fe_version", "nmea_version", "interface_ids", "expected_storage", "msg_rates", "set_config",
                           "set_config_exhaustive", "import_config", "reboot", "watchdog_fault", "save_config"]
                ),
            ])
    else:
        interface_name = {DeviceType.ATLAS: 'tcp1', DeviceType.ZIPLINE: 'tcp3'}.get(env_args.HITL_BUILD_TYPE)
        test_set = ["fe_version", "interface_ids", "expected_storage", "msg_rates", "set_config",
                    "set_config_exhaustive", "import_config", "save_config"]

        if interface_name is None:
            logger.error('Unable to extract interface name.')
            return False

        # TODO: Add Atlas reboot and watchdog support.
        test_config = TestConfig(
            config=ConfigSet(
                devices=[device_config]
            ),
            tests=[
                InterfaceTests(
                    name=device_config.name,
                    interface_name=interface_name,
                    tests=test_set
                )
            ])

    # Copy shared settings to each interface to simplify checks.
    copy_shared_settings_to_devices(test_config.config)
    try:
        for interface_tests in test_config.tests:
            logger.info(f'Running tests on device {interface_tests.name} ({interface_tests.interface_name}).')

            device_to_test_config: Optional[DeviceConfig] = None
            for device_config in test_config.config.devices:
                if device_config.name == interface_tests.name:
                    device_to_test_config = device_config
                    break

            if device_to_test_config is None:
                logger.error(f'No device config name matched name for tests: {interface_tests.name}')
                return False

            interface_idx = None
            if interface_tests.interface_name is not None:
                interface_idx = INTERFACE_MAP.get(interface_tests.interface_name)
                if interface_idx is None:
                    logger.error(f'No interface known with name: {interface_tests.interface_name}')
                    return False

            log_start_data_offset = get_log_size(logger_manager)

            data_source = open_data_source(device_to_test_config)
            if data_source is None:
                return False

            try:
                data_source.rx_log = logger_manager  # type: ignore
                interface = DeviceInterface(data_source)

                interface_name = interface_tests.interface_name if interface_tests.interface_name is not None else "current"

                state = TestState(
                    device_interface=interface, test_logger=logger_manager, interface_name=interface_name,
                    interface_idx=interface_idx, config=device_to_test_config)

                time.sleep(0.2)
                for test_name in interface_tests.tests:
                    logger.info(f"============ Checking {test_name}. ============")
                    # This is the magic that checks the tests against the functions in this file.
                    test_func = getattr(module, 'test_' + test_name, None)
                    if test_func is None:
                        logger.error('Invalid test %s.', test_name)
                        return False
                    else:
                        # Raise FatalMetricException on failures
                        test_func(state)
                    # Make sure there's some time between each test.
                    time.sleep(0.2)

                logger.info("Checking captured log.")
                logged_data_check(state, log_start_data_offset)
            finally:
                data_source.stop()
    except FatalMetricException:
        pass
    except Exception as e:
        logger.error(f'Exception while running config tests:\n{traceback.format_exc()}')
        return False

    metric_run_configuration_check.check(True)
    return True
