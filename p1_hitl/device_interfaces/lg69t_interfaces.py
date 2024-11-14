import logging
import sys
import time
from argparse import Namespace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Optional

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from bin.config_tool import apply_config, request_reset, save_config
from firmware_tools.lg69t.firmware_tool import run_update
from p1_hitl.defs import HitlEnvArgs
from p1_hitl.get_build_artifacts import download_file
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.devices_config import (DeviceConfig, RelayConfig,
                                               open_data_source)
from p1_test_automation.relay_controller import RelayController

from .base_interfaces import HitlDeviceInterfaceBase

RESTART_WAIT_TIME_SEC = 15

logger = logging.getLogger('point_one.hitl.lg69t_interface')


class HitlLG69TInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_UART1', 'JENKINS_UART2', 'JENKINS_RESET_RELAY']):
            return None
        else:
            assert args.JENKINS_RESET_RELAY is not None  # For type check.
            return DeviceConfig(name=args.HITL_NAME,
                                serial_port=args.JENKINS_UART2,
                                reset_relay=RelayConfig(
                                    id=args.JENKINS_RESET_RELAY[0],
                                    relay_number=args.JENKINS_RESET_RELAY[1]
                                ))

    def __init__(self, config: DeviceConfig):
        self.config = config
        self.device_interface: Optional[DeviceInterface] = None

    def init_device(self, build_info: Dict[str, Any], skip_reset=False) -> Optional[DeviceInterface]:
        # build_info example:
        # {
        #     "timestamp": 1725918926,
        #     "version": "v2.1.0-917-g7e74d1b235",
        #     "git_hash": "7e74d1b2356165d0e4408aa665ebf214e8a6dcb3",
        #     "aws_path": "s3://pointone-build-artifacts/nautilus/quectel/v2.1.0-917-g7e74d1b235/"
        # }

        # TODO: Power cycle at some point?.

        # TODO: Add factory reset logic.
        logger.info(f'Initializing LG69T.')

        with NamedTemporaryFile(suffix='.p1fw') as tmp_file:
            if not download_file(tmp_file, build_info['aws_path'], r'.*\.p1fw'):
                return None

            def _reboot_cmd(relay_config: RelayConfig):
                ctrl = RelayController(relay_config.relay_number, relay_id=relay_config.id)
                ctrl.send_cmd(True)
                ctrl.send_cmd(False)

            args = Namespace(
                file=tmp_file.name,
                force=False,
                manual_reboot=False,
                release=False,
                suppress_progress=True,
                type=None,
                show=False,
                gnss=None,
                app=None,
                port=self.config.serial_port,
                reboot_cmd=lambda: _reboot_cmd(self.config.reset_relay),  # type: ignore
            )
            # Sysexits on failure
            applied_update = run_update(args)
            if applied_update:
                time.sleep(RESTART_WAIT_TIME_SEC)

        data_source = open_data_source(self.config)
        if data_source is None:
            logger.error(f"Can't open Quectel serial interface.")
            return None

        device_interface = DeviceInterface(data_source)
        logger.info('Clearing FE settings.')
        args = Namespace(revert_to_saved=False, revert_to_defaults=True)
        if not save_config(device_interface, args):
            logger.error('Clearing FE settings failed.')
            return None

        logger.info('Enabling diagnostics')
        args = Namespace(interface_config_type='diagnostics_enabled', param='current', enabled=True, save=True)
        if not apply_config(device_interface, args):
            logger.error('Enabling diagnostics failed.')
            return None

        if not skip_reset:
            logger.info('Restarting Quectel with diagnostic reset')
            args = Namespace(type=['diag'])
            if not request_reset(device_interface, args):
                logger.error('Reset failed.')
                return None
            # Sleep to give restarted software a chance to get up and running.
            time.sleep(RESTART_WAIT_TIME_SEC)

        self.device_interface = device_interface
        return device_interface

    def shutdown_device(self, tests_passed: bool, output_dir: Path):
        # Nothing logged on device to dump.
        return
