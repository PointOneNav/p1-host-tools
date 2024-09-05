#!/usr/bin/env python3

import copy
import os
import re
import struct
import sys
import tempfile
from argparse import Namespace
from enum import IntEnum
from pprint import pprint
from typing import Any, Dict, List, Optional

import construct
from deepdiff import DeepDiff

# isort: split
from fusion_engine_client.messages import (DataType,
                                           PlatformStorageDataMessage,
                                           Response, VersionInfoMessage)

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# isort: split
from bin.config_tool import query_fe_version, request_export, request_import
from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction
from p1_runner.device_interface import DeviceInterface
from p1_runner.config_loader_helpers import get_config_loader_for_device, user_config_from_platform_storage

logger = logging.getLogger('point_one.test_automation.manage_configs')

common_version_check_data = VersionInfoMessage()


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


def check_version_str(
    version_str: str, shared_expected_version_re: Optional[str], device_expected_version_re: Optional[str]
) -> bool:
    # Device version check takes priority over shared check.
    if device_expected_version_re is not None:
        expected_version_re = device_expected_version_re
    elif shared_expected_version_re is not None:
        expected_version_re = shared_expected_version_re
    else:
        return True

    m = re.match(expected_version_re, version_str)
    if m is None:
        logger.error('Version response %s did not match expected version %s.', version_str, expected_version_re)
        return False
    else:
        return True


def check_version(device_interface: DeviceInterface, device_config: DeviceConfig, shared_config: SharedConfig) -> bool:
    """!
    @brief Tests that the version request messages are working.

    In addition, it checks if the reported version matches the @ref TestConfig
    expected_version regex if specified.
    """
    args = Namespace()
    logger.debug("Checking version.")
    resp = query_fe_version(device_interface, args)
    if not isinstance(resp, VersionInfoMessage):
        logger.error('Version request failed.')
        return False
    else:
        version_str = resp.engine_version_str
        logger.info(f'Engine version: "{version_str}"')
        if not check_version_str(
            version_str, shared_config.expected_engine_version, device_config.expected_engine_version
        ):
            return False

        version_str = resp.fw_version_str
        logger.info(f'Firmware version: "{version_str}"')
        if not check_version_str(version_str, shared_config.expected_fw_version, device_config.expected_fw_version):
            return False

        if shared_config.expect_same_versions_on_devices:
            if len(common_version_check_data.engine_version_str) == 0:
                common_version_check_data.engine_version_str = resp.engine_version_str
                common_version_check_data.fw_version_str = resp.fw_version_str
            else:
                if (
                    common_version_check_data.engine_version_str != resp.engine_version_str
                    or common_version_check_data.fw_version_str != resp.fw_version_str
                ):
                    logger.error(
                        'Version response %s did not match version of other devices %s.',
                        resp,
                        common_version_check_data,
                    )
                    return False

    return True


def check_storage(
    device_interface: DeviceInterface, device_config: DeviceConfig, shared_config: SharedConfig, update_prompt: str
) -> bool:
    """!
    @brief Checks the calibration and UserConfig saved on the device matches the @ref TestConfig.

    This does 3 optional checks:
     - Tests if the active configuration is modified from the saved.
     - Tests if the saved configuration matches values loaded from an export file.
     - Tests if the device has a completed calibration.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = os.path.join(tmp, 'dummy.p1nvm')

        logger.debug("Getting UserConfig loader.")
        UserConfig = get_config_loader_for_device(device_interface)
        if UserConfig is None:
            return False

        logger.debug("Reading active UserConfig on device.")
        args = Namespace(type='user_config', format="p1nvm", export_file=tmp_path, export_source='active')
        active_storage: Optional[List[PlatformStorageDataMessage]] = request_export(device_interface, args)
        active_config = None
        if active_storage is None:
            logger.error('Export failed.')
            return False
        else:
            active_config = active_storage[0]

        logger.debug("Reading all saved storage on device.")
        args = Namespace(type='all', format="p1nvm", export_file=tmp_path, export_source='saved')
        saved_storage: Optional[List[PlatformStorageDataMessage]] = request_export(device_interface, args)
        saved_config = None
        saved_calibration_data = None
        if saved_storage is None:
            logger.error('Export failed.')
            return False

        for storage in saved_storage:
            if storage.data_type == DataType.CALIBRATION_STATE:
                saved_calibration_data = storage.data
            elif storage.data_type == DataType.USER_CONFIG:
                saved_config = storage

        if saved_config is None:
            logger.error('Missing expected saved UserConfig.')
            return False

        if saved_config.response != Response.OK:
            logger.error(f'Invalid saved storage data {saved_config.response.name}.')
            return False

        if saved_config.data != active_config.data:
            logger.error('Active configuration differs from saved configuration.')
            return False
        else:
            logger.info("Config has no unsaved changes.")

        if device_config.expect_calibration_done:
            logger.debug("Checking if calibration is DONE.")
            if saved_calibration_data is not None:
                stage = get_calibration_stage(saved_calibration_data)
                if stage != CalibrationStage.DONE:
                    logger.error('Expected calibration to be done. Got stage %s.', stage.name)
                    return False
            else:
                logger.error('No valid saved calibration found.')
                return False
        elif saved_calibration_data is not None:
            stage = get_calibration_stage(saved_calibration_data)
            logger.info(f'Calibration stage: {stage.name}.')
        else:
            logger.info('No calibration saves on device.')

        logger.debug('Load device defaults.')
        args = Namespace(type='user_config', format="p1nvm", export_file=tmp_path, export_source='default')
        default_storage: Optional[List[PlatformStorageDataMessage]] = request_export(device_interface, args)
        default_config = None
        if default_storage is None:
            logger.error('Export failed.')
            return False
        else:
            default_config = default_storage[0]

        default_conf = user_config_from_platform_storage(default_config, UserConfig)
        saved_conf = user_config_from_platform_storage(saved_config, UserConfig)
        if default_conf is None or saved_conf is None:
            return False

        expected_conf = copy.deepcopy(default_conf)
        unused = expected_conf.update(shared_config.modified_settings)
        if unused is not None and len(unused) > 0:
            logger.error(f'Invalid shared_config modified_settings: {unused}')
            return False
        unused = expected_conf.update(device_config.modified_settings)
        if unused is not None and len(unused) > 0:
            logger.error(f'Invalid device modified_settings: {unused}')
            return False

        conf_diff = DeepDiff(
            saved_conf,
            expected_conf,
            ignore_nan_inequality=True,
            ignore_numeric_type_changes=True,
            math_epsilon=0.00001,
            ignore_type_in_groups=[(list, construct.lib.containers.ListContainer)],
        )

        if len(conf_diff) > 0:
            pprint(conf_diff, indent=2)

            if update_prompt == 'fail':
                return False

            if update_prompt == 'ask':
                while True:
                    resp = input('Update device to expected config?\n"u"=update, "s"=skip, "e"=exit: ').lower()
                    if resp == 's':
                        return True
                    elif resp == 'e':
                        exit(1)
                    elif resp == 'u':
                        break

            logger.info('Updating configuration to expected values.')
            json_tmp_path = os.path.join(tmp, 'dummy.json')
            with open(json_tmp_path, 'w') as fd:
                fd.write(expected_conf.to_json())

            args = Namespace(
                type='user_config',
                preserve_unspecified=False,
                file=json_tmp_path,
                dry_run=False,
                force=True,
                dont_save_config=False,
            )
            if not request_import(device_interface, args):
                logger.error('Update failed.')
                return False
        else:
            logger.info("Device using expected configuration.")

    return True


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]...' % execute_command,
        description='Connect to devices and validate their configuration state.',
    )

    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase " "verbosity.",
    )
    parser.add_argument(
        '-c',
        '--continue-on-failures',
        action=ExtendedBooleanAction,
        help="Continue checking devices even if one has errors.",
    )
    parser.add_argument(
        '-d',
        '--device-configurations',
        default=None,
        help="A JSON file with the configuration for the devices to validate. See 'p1_runner/device_config.py' for details.",
    )
    parser.add_argument(
        '-u',
        '--update-prompt',
        choices=['ask', 'force', 'fail'],
        default='ask',
        help="""\
The action to take if the configuration doesn't match the expected values.
 - "ask" - Prompt an input on stdin on whether to update and save the devices configuration.
 - "force" - Update and save the devices configuration automatically.
 - "fail" - Consider a configuration miss match an error and don't try to modify the device.""",
    )

    args = parser.parse_args()

    if args.verbose == 0:
        logger.setLevel(logging.INFO)
        logging.basicConfig(
            level=logging.INFO, format='[%(levelname).1s %(filename)s:%(lineno)d] %(message)s', stream=sys.stdout
        )
    else:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(
            level=logging.INFO,
            format='[%(filename)s:%(lineno)-4d] %(asctime)s - %(levelname)-8s - %(message)s',
            stream=sys.stdout,
        )

    if args.verbose < 2:
        logging.getLogger('point_one.config_tool').setLevel(logging.WARNING)
        logging.getLogger('point_one.exported_data').setLevel(logging.WARNING)
    elif args.verbose == 2:
        pass
    elif args.verbose == 3:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.DEBUG)
        logging.getLogger('point_one.device_interface').setLevel(logging.DEBUG)
    else:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.TRACE)
        logging.getLogger('point_one.device_interface').setLevel(logging.TRACE)

    config = load_config_set(args.device_configurations)

    errors = 0

    for device in config.devices:
        logger.info('######## Checking configuration for %s. ########' % (device.name))

        data_source = open_data_source(device)
        device_interface = DeviceInterface(data_source) if data_source is not None else None

        if (
            device_interface is not None
            and check_version(device_interface, device, config.shared)
            and check_storage(device_interface, device, config.shared, args.update_prompt)
        ):
            logger.info('######## Completed checking configuration for %s. ########' % (device.name))
        else:
            logger.info('######## Failure checking configuration for %s. ########' % (device.name))
            errors += 1
            if not args.continue_on_failures:
                exit(1)

    logger.info(f'######## All devices completed, with {errors} errors. ########')


if __name__ == "__main__":
    main()
