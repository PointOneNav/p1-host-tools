#!/usr/bin/env python3

import concurrent.futures
import os
import sys
import time
from argparse import Namespace
from typing import Optional, List

from balena import Balena

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# Put imports that rely on this in their own indent block to avoid linter reordering.
# isort: split
from bin.config_tool import request_shutdown, request_startup, request_reset
from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction, TriStateBooleanAction
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.atlas_device_ctrl import (
    CrashLogAction,
    LoggingCmd,
    enable_rolling_logs,
    restart_application,
    send_logging_cmd_to_legacy_atlas,
    set_crash_log_action,
)
from p1_test_automation.devices_config import TruthType, DeviceConfig, load_config_set, open_data_source

logger = logging.getLogger('point_one.test_automation.devices_send_cmd')

MAX_THREADS = 32

def get_interface(device_config: DeviceConfig) -> Optional[DeviceInterface]:
    # Open data source.
    data_source = open_data_source(device_config)
    if data_source is None:
        logger.error("Error connecting to device at address %s." % device_config.tcp_address)
        return None
    return DeviceInterface(data_source)


def start_logging(device_config: DeviceConfig, args):
    interface = get_interface(device_config)
    if interface is not None and not request_startup(interface, args):
        logger.error("Startup request failed.")
        return False

    return True


def send_reset(device_config: DeviceConfig, args):
    interface = get_interface(device_config)
    if interface is not None and not request_reset(interface, args):
        logger.error("Reset request failed.")
        return False

    return True


def stop_logging(device_config: DeviceConfig, args):
    interface = get_interface(device_config)
    if interface is not None and not request_shutdown(interface, args):
        logger.error("Shutdown request failed.")
        return False

    return True


def send_cmd_function(device_config: DeviceConfig, args: Namespace, balena: Optional[Balena]) -> bool:
    command_successful = True
    if args.command == "log":
        namespace_args = Namespace()
        namespace_args.type = 'log'

        enabled = (
            args.enable_rolling_logs if args.enable_rolling_logs is not None else device_config.rolling_logs_enabled
        )
        if enabled is not None:
            if device_config.tcp_address is None:
                logger.error(f"Can't enable rolling logs for device {device_config.name} without tcp_address.")
            else:
                command_successful = enable_rolling_logs(device_config.tcp_address, enabled)

        if command_successful:
            # Start/stop logging.
            if args.action == 'start':
                if args.restart_type == 'none':
                    command_successful = start_logging(device_config, namespace_args)
                else:
                    command_successful = restart_application(device_config.tcp_address, log_on_startup=True)
            elif args.action == 'stop':
                command_successful = stop_logging(device_config, namespace_args)
    elif args.command == "reset":
        command_successful = send_reset(device_config, args)
    elif args.command == "balena_pin_release":
        if device_config.balena is not None and device_config.balena.pinned_release is not None:
            balena.models.device.pin_to_release(device_config.balena.uuid, device_config.balena.pinned_release)
        else:
            logger.info(f'No balena release specified for {device_config.name}.')
    elif args.command == "set_crash_log_action":
        action = CrashLogAction[args.action]
        if device_config.tcp_address is None:
            logger.error(f"Can't set crash log action for device {device_config.name} without tcp_address.")
        else:
            command_successful = set_crash_log_action(device_config.tcp_address, action)
    elif args.command == "balena_get_status":
        if device_config.balena is not None:
            # See https://docs.balena.io/reference/sdk/python-sdk/#typedevice
            balena_device = balena.models.device.get(device_config.balena.uuid)
            logger.debug(balena_device)
            print(f"Balena name: {balena_device['device_name']}")
            if device_config.balena.pinned_release:
                # See https://docs.balena.io/reference/sdk/python-sdk/#releasetype
                target_release = balena.models.release.get(device_config.balena.pinned_release)
                logger.debug(target_release)
                print(
                    f"Device matches pinned release: {target_release['id'] == balena_device['should_be_running__release']['__id']}"
                )
            print(f"Device online: {balena_device['is_online']}")
            print(
                f"Is Updating: {balena_device['should_be_running__release']['__id'] != balena_device['is_running__release']['__id']}"
            )
        else:
            logger.info(f'No balena release specified for {device_config.name}.')

    if not command_successful:
        logger.error("Command unsuccessful for device %s." % device_config.name)
        return False
    else:
        logger.info("Command successful for device %s." % device_config.name)

    if args.command == "balena_pin_release" and args.wait:
        if device_config.balena is not None and device_config.balena.pinned_release:
            while True:
                balena_device = balena.models.device.get(device_config.balena.uuid)
                if not balena_device['is_online']:
                    logger.info(f'Skipping {device_config.name} since it is offline.')
                    break
                else:
                    if (
                        balena_device['should_be_running__release']['__id']
                        == balena_device['is_running__release']['__id']
                    ):
                        logger.info(f'{device_config.name} finished updating.')
                        break
                    else:
                        logger.info(f'Waiting for {device_config.name} to finish updating.')
                        time.sleep(10)
    return True


def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-d',
        '--device-configurations',
        default=None,
        help="A JSON file with the configuration for the devices to display.",
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase verbosity.",
    )
    command_subparsers = parser.add_subparsers(dest='command', help='The command to be run.')

    help = 'Send a logging command.'
    log_parser = command_subparsers.add_parser('log', help=help, description=help)
    log_parser.add_argument(
        'action',
        metavar='ACTION',
        default='start',
        type=str,
        choices=['start', 'stop'],
        help=f"""\
The type of logging action to be performed:
start - Start a new log on each device. Disable rolling logs.
stop - Stop any active log on each device
""",
    )
    log_parser.add_argument(
        "--restart-type",
        choices=['none', 'deterministic'],
        default='deterministic',
        help='The type of reset to perform when starting the log.',
    )
    log_parser.add_argument(
        "--enable-rolling-logs",
        action=TriStateBooleanAction,
        help='Enable/disable rolling logs, ignoring the device configuration.',
    )

# config_tool.py reset
    help = 'Issue a device reset request.'
    reset_parser = command_subparsers.add_parser(
        'reset',
        help=help,
        description=help)

    choices = {
        'factory': 'Reset all settings back to factory defaults.',
        'hot': 'Perform a hot start, keeping GNSS ephemeris data, and approximate position/time knowledge where '
               'applicable',
        'warm': 'Perform a warm start, resetting GNSS ephemeris data, and approximate position/time knowledge where '
                'applicable',
        'pvt': 'Reset all position, velocity, orientation, time, and GNSS corrrections information',
        'cold': 'Perform a cold start, resetting position/time information and GNSS ephemeris/corrections data',
        'diag': 'Reset to a deterministic state for diagnostic and post-processing purposes',
        'calibration': 'Reset all calibration and navigation state data',
        'config': 'Reset all user configuration data (this will also reset calibration and navigation state data)',
        'nav_engine': 'Reset the navigation engine: clear all PVT data, plus sensor corrections (IMU and wheel speed)',
        'position': 'Reset position, velocity, and orientation data',
        'reboot': 'Reboot the navigation processor',
        'reboot_gnss': 'Reboot the GNSS measurement engine',
    }
    newline = '\n'
    reset_parser.add_argument(
        'type', metavar='TYPE',
        choices=choices.keys(),
        nargs='+',
        default='cold',
        help=f"""\
The type of reset to be performed: {''.join([f'{newline}- {k} - {v}' for k, v in choices.items()])}

You may specify more than one value to reset multiple components. For example, to perform a warm start and also a
diagnostic log reset:
  ... reset diag cold""")

    help = 'Set crash log action.'
    log_parser = command_subparsers.add_parser('set_crash_log_action', help=help, description=help)
    log_parser.add_argument(
        'action',
        metavar='ACTION',
        default='start',
        type=str,
        choices=[v.name for v in CrashLogAction],
        help=f"""\
The type of action to be performed on crash logs:
FULL_LOG - Upload and delete all crash logs. This applies to logs currently on the device and any that occur in the
           future.
MANIFEST_ONLY - Upload the manifest for any crashes that occur. Leave logs on device until this setting is
                changed to FULL_LOG or device runs out of space.
NONE - Leave logs on device until this setting is changed to FULL_LOG or device runs out of space.
""",
    )

    help = 'Update the pinned Balena releases.'
    balena_release_parser = command_subparsers.add_parser('balena_pin_release', help=help, description=help)
    balena_release_parser.add_argument(
        "--wait", action=ExtendedBooleanAction, help='Block until devices finish updating.'
    )

    help = 'Check on the device status.'
    balena_status_parser = command_subparsers.add_parser('balena_get_status', help=help, description=help)

    args = parser.parse_args()

    if args.verbose == 0:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    else:
        logging.basicConfig(
            level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout
        )
        logger.setLevel(logging.DEBUG)

    config = load_config_set(args.device_configurations)
    command_successful = False

    if 'balena' in args.command:
        balena = Balena()
        token = balena.auth.get_token()
        if token is None:
            logger.error("Can't load Balena token.")
            exit(1)
        balena.auth.login_with_token(token)
    else:
        balena = None

    # Start/stop truth source.
    if config.truth is not None:
        truth_source = config.truth
        if args.command == "log" and truth_source.type == TruthType.DEVELOP_ATLAS:
            log_cmd = LoggingCmd.START if args.action == 'start' else LoggingCmd.STOP
            command_successful = send_logging_cmd_to_legacy_atlas(truth_source.tcp_address, log_cmd)
            if not command_successful:
                # Return early if the truth device isn't responding as expected.
                logger.error("Start/stop command unsuccessful for truth device %s." % truth_source.name)
                exit(1)

    # Start running commands in thread pool.
    futures: List[concurrent.futures.Future] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
        for device_config in config.devices:
            futures.append(
                executor.submit(
                    send_cmd_function,
                    device_config=device_config,
                    args=args,
                    balena=balena,
                )
            )

    # Wait for cmds to complete.
    all_successes = True
    for future, device_config in zip(futures, config.devices):
        try:
            all_successes &= future.result()
        except Exception as exc:
            logger.error(f'Sending command to {device_config.name} generated an exception: {exc}')


    if all_successes:
        exit(0)
    else:
        exit(1)


if __name__ == "__main__":
    main()
