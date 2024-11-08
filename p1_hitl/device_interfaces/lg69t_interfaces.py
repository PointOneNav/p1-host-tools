import logging
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, Dict, Optional
from tempfile import NamedTemporaryFile

import boto3

from firmware_tools.lg69t.firmware_tool import run_update
from bin.config_tool import apply_config, request_shutdown, save_config, query_version
from p1_hitl.defs import UPLOADED_LOG_LIST_FILE, HitlEnvArgs
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.devices_config import (BalenaConfig, DeviceConfig,
                                               open_data_source)
from p1_hitl.get_build_artifacts import download_file

from .base_interfaces import HitlDeviceInterfaceBase

RESTART_WAIT_TIME_SEC = 10

logger = logging.getLogger('point_one.hitl.lg69t_interface')


class HitlLG69TInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_UART1', 'JENKINS_UART2']):
            return None
        else:
            return DeviceConfig(name=args.HITL_NAME,
                                serial_port=args.JENKINS_UART1,
                                )

    def __init__(self, config: DeviceConfig):
        self.config = config
        self.device_interface: Optional[DeviceInterface] = None

    def init_device(self, build_info: Dict[str, Any], skip_reset=False) -> Optional[DeviceInterface]:
        # build_info example:
        # {
        #     "timestamp": 1725918926,
        #     "version": "v2.1.0-917-g7e74d1b235",
        #     "git_hash": "7e74d1b2356165d0e4408aa665ebf214e8a6dcb3",
        #     "aws_path": "s3://pointone-build-artifacts/nautilus/atlas/v2.1.0-917-g7e74d1b235/"
        # }

        # TODO: Power cycle at some point?.

        # TODO: Add factory reset logic.
        logger.info(f'Initializing LG69T.')


        with NamedTemporaryFile(suffix='.p1fw') as tmp_file:
            if not download_file(tmp_file, build_info['aws_path'], r'.*\.p1fw'):
                return None
            args = Namespace(file=tmp_file.name,force=False,manual_reboot=False,release=False,suppress_progress=True,type=None,show=False,gnss=None, app=None, port=self.config.serial_port)
            # Sysexits on failure
            run_update(args)



        raise NotImplementedError()

        # TODO: Decide how to detect Teseo updates are needed, and perform them.

        # data_source = open_data_source(self.config)
        # if data_source is None:
        #     logger.error(f"Can't open Atlas TCP interface: {self.config.tcp_address}.")
        #     return None

        # set_crash_log_action(self.config.tcp_address, CrashLogAction.FULL_LOG)  # type: ignore

        # device_interface = DeviceInterface(data_source)
        # logger.info('Clearing FE settings.')
        # args = Namespace(revert_to_saved=False, revert_to_defaults=True)
        # if not save_config(device_interface, args):
        #     logger.error('Clearing FE settings failed.')
        #     return None

        # logger.info('Enabling diagnostics')
        # args = Namespace(interface_config_type='diagnostics_enabled', param='current', enabled=True, save=True)
        # if not apply_config(device_interface, args):
        #     logger.error('Enabling diagnostics failed.')
        #     return None
        # data_source.stop()

        # log_status = get_log_status(self.config.tcp_address)  # type: ignore
        # if log_status is None:
        #     logger.error('Error querying logs.')
        #     return None
        # self.old_log_guids = {l['guid'] for l in log_status['logs']}

        # if not skip_reset:
        #     logger.info('Restarting Atlas with diagnostic logging')
        #     # Restart nautilus container with logging enabled at startup.
        #     if not restart_application(self.config.tcp_address, log_on_startup=True):
        #         logger.error('Atlas restart failed.')
        #         return None
        #     # Sleep to give restarted software a chance to get up and running.
        #     time.sleep(RESTART_WAIT_TIME_SEC)

        # data_source = open_data_source(self.config)
        # if data_source is None:
        #     logger.error(f"Can't reopen Atlas TCP interface: {self.config.tcp_address}.")
        #     return None
        # self.device_interface = DeviceInterface(data_source)
        # return self.device_interface

    def shutdown_device(self, tests_passed: bool, output_dir: Path):
        # Nothing logged on device to dump.
        return
