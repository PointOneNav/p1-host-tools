import logging
import time
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from typing import Any, Dict, Optional

from bin.config_tool import request_shutdown
from p1_hitl.defs import UPLOADED_LOG_LIST_FILE, HitlEnvArgs
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.atlas_device_ctrl import (AtlasBalenaController,
                                                  CrashLogAction,
                                                  factory_reset,
                                                  get_log_status,
                                                  restart_application,
                                                  set_crash_log_action,
                                                  upload_log)
from p1_test_automation.devices_config import (BalenaConfig, DeviceConfig,
                                               open_data_source)

from .base_interfaces import HitlDeviceInterfaceBase

UPDATE_TIMEOUT_SEC = 60 * 20
CMD_POLL_INTERVAL_SEC = 10
RESTART_TIMEOUT_SEC = 60 * 2
RESTART_WAIT_TIME_SEC = 30

DIAGNOSTIC_PORT = 30202

logger = logging.getLogger('point_one.hitl.atlas_interface')


def cmd_with_retries(cmd: Callable[[], bool], timeout: float) -> bool:
    logger.info(f'Will retry command for {timeout}s to wait for containers to startup.')
    start_time = time.monotonic()
    while True:
        if cmd():
            return True
        else:
            elapsed = time.monotonic() - start_time
            if elapsed > timeout:
                return False
            else:
                logger.info('Retrying command.')
                time.sleep(CMD_POLL_INTERVAL_SEC)


class HitlAtlasInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_LAN_IP', 'JENKINS_ATLAS_BALENA_UUID']):
            return None
        else:
            balena_uuid: str = args.JENKINS_ATLAS_BALENA_UUID  # type: ignore # Already did None check.
            return DeviceConfig(name=args.HITL_NAME,
                                tcp_address=args.JENKINS_LAN_IP,
                                port=DIAGNOSTIC_PORT,
                                balena=BalenaConfig(uuid=balena_uuid),
                                )

    def __init__(self, config: DeviceConfig, env_args: HitlEnvArgs):
        self.old_log_guids: set[str] = set()
        self.config = config
        self.device_interface: Optional[DeviceInterface] = None

    def init_device(self, build_info: Dict[str, Any], skip_reset=False,
                    skip_corrections=False) -> Optional[DeviceInterface]:
        # build_info example:
        # {
        #     "timestamp": 1725918926,
        #     "version": "v2.1.0-917-g7e74d1b235",
        #     "git_hash": "7e74d1b2356165d0e4408aa665ebf214e8a6dcb3",
        #     "aws_path": "s3://pointone-build-artifacts/nautilus/atlas/v2.1.0-917-g7e74d1b235/",
        #     "balena_release": "1486fb4fa623aaf600a5f1130f07dbe6"
        # }

        # TODO: Power cycle at some point?.

        # TODO: Disable corrections if needed.

        logger.info(f'Initializing Atlas.')

        if self.config.balena is None:
            raise KeyError('Config missing Balena UUID.')

        balena_ctrl = AtlasBalenaController()
        release_str = build_info['balena_release']
        status = balena_ctrl.get_status(self.config.balena.uuid)

        if not status.is_online:
            logger.error(f'Atlas {status.name} reported as offline by Balena.')
            return None

        target_release = balena_ctrl.get_release(release_str)

        if target_release is None:
            logger.error(f"Release {release_str} is not a valid Balena release.")
            return None
        elif target_release == status.current_release:
            logger.info(f'Atlas {status.name} already running target release.')
        else:
            logger.info(f'Updating Atlas {status.name} to target release.')
            balena_ctrl.pin_release(self.config.balena.uuid, target_release.id)

            start_time = time.monotonic()
            while True:
                if time.monotonic() > start_time + UPDATE_TIMEOUT_SEC:
                    logger.error(f'Atlas {status.name} update timed out after {UPDATE_TIMEOUT_SEC} seconds.')
                    return None
                status = balena_ctrl.get_status(self.config.balena.uuid)

                if target_release == status.current_release:
                    logger.info(f'{status.name} finished updating.')
                    break
                else:
                    time.sleep(CMD_POLL_INTERVAL_SEC)

        if not skip_reset:
            logger.info(f'Sending factory reset.')
            if not cmd_with_retries(lambda: factory_reset(self.config.tcp_address,
                                    reset_networking=True), RESTART_TIMEOUT_SEC):
                logger.error('Factory reset failed.')
                return None
            time.sleep(RESTART_WAIT_TIME_SEC)

        logger.info(f'Setting crash log upload enabled.')
        if not cmd_with_retries(lambda: set_crash_log_action(self.config.tcp_address,  # type: ignore
                                CrashLogAction.FULL_LOG), RESTART_TIMEOUT_SEC):
            logger.error('Applying Nemo setting failed.')
            return None

        log_status = get_log_status(self.config.tcp_address)  # type: ignore
        if log_status is None:
            logger.error('Error querying logs.')
            return None
        self.old_log_guids = {l['guid'] for l in log_status['logs']}

        if not skip_reset:
            logger.info('Restarting Atlas with diagnostic logging')
            # Restart nautilus container with logging enabled at startup.
            if not restart_application(self.config.tcp_address, log_on_startup=True):
                logger.error('Atlas restart failed.')
                return None
            # Sleep to give restarted software a chance to get up and running.
            time.sleep(RESTART_WAIT_TIME_SEC)

        data_source = open_data_source(self.config)
        if data_source is None:
            logger.error(f"Can't reopen Atlas TCP interface: {self.config.tcp_address}.")
            return None
        self.device_interface = DeviceInterface(data_source)
        return self.device_interface

    def shutdown_device(self, tests_passed: bool, output_dir: Path):
        if self.config.tcp_address is None or self.device_interface is None:
            return

        namespace_args = Namespace()
        namespace_args.type = 'log'
        request_shutdown(self.device_interface, namespace_args)
        self.device_interface.data_source.stop()

        # Upload new device logs after failures.
        if not tests_passed:
            log_status = get_log_status(self.config.tcp_address)
            if log_status is None:
                logger.error('Error querying logs.')
                return
            with open(output_dir / UPLOADED_LOG_LIST_FILE, 'w') as fd:
                for log in log_status['logs']:
                    if log['guid'] not in self.old_log_guids:
                        upload_log(self.config.tcp_address, log['guid'])
                        logger.warning(f'Uploading device log: {log["guid"]}')
                        fd.write(f'{log["guid"]}\n')
