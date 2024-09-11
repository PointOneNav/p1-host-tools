import logging
import time
from typing import Any, Dict, Optional

from p1_hitl.defs import HiltEnvArgs
from p1_hitl.device_init import DeviceInitBase
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.atlas_device_ctrl import AtlasBalenaController
from p1_test_automation.devices_config import (BalenaConfig, DeviceConfig,
                                               open_data_source)

UPDATE_TIMEOUT_SEC = 60 * 10


logger = logging.getLogger('point_one.hitl.atlas_init')


class AtlasInit(DeviceInitBase):
    @staticmethod
    def get_device_config(args: HiltEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_ATLAS_LAN_IP', 'JENKINS_ATLAS_BALENA_UUID']):
            return None
        else:
            balena_uuid: str = args.JENKINS_ATLAS_BALENA_UUID  # type: ignore # Already did None check.
            return DeviceConfig(name=args.HITL_NAME,
                                modified_settings={},
                                tcp_address=args.JENKINS_ATLAS_LAN_IP,
                                balena=BalenaConfig(balena_uuid),
                                )

    @staticmethod
    def init_device(config: DeviceConfig, build_info: Dict[str, Any]) -> Optional[DeviceInterface]:
        # build_info example:
        # {
        #     "timestamp": 1725918926,
        #     "version": "v2.1.0-917-g7e74d1b235",
        #     "git_hash": "7e74d1b2356165d0e4408aa665ebf214e8a6dcb3",
        #     "aws_path": "s3://pointone-build-artifacts/nautilus/atlas/v2.1.0-917-g7e74d1b235/",
        #     "balena_release": "1486fb4fa623aaf600a5f1130f07dbe6"
        # }

        # TODO: Power cycle at some point?.

        # TODO: Add factory reset logic.
        logger.info(f'Initializing Atlas.')

        if config.balena is None:
            raise KeyError('Config missing Balena UUID.')

        balena_ctrl = AtlasBalenaController()
        release_str = build_info['balena_release']
        status = balena_ctrl.get_status(config.balena.uuid)

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
            balena_ctrl.pin_release(config.balena.uuid, target_release.id)

            start_time = time.time()
            while True:
                if time.time() > start_time + UPDATE_TIMEOUT_SEC:
                    logger.error(f'Atlas {balena_status.name} update timed out after {UPDATE_TIMEOUT_SEC} seconds.')
                    return None
                balena_status = balena_ctrl.get_status(config.balena.uuid)

                if target_release == balena_status.current_release:
                    logger.info(f'{balena_status.name} finished updating.')
                    break

        data_source = open_data_source(config)
        if data_source is None:
            logger.error(f"Can't open Atlas TCP interface: {config.tcp_address}.")
            return None

        return DeviceInterface(data_source)
