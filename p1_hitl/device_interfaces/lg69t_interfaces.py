import logging
import os
import sys
import threading
import time
from argparse import Namespace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, Optional

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from bin.config_tool import request_reset
from firmware_tools.lg69t.firmware_tool import run_update
from p1_hitl.defs import HitlEnvArgs
from p1_hitl.get_build_artifacts import download_file
from p1_runner.device_interface import DeviceInterface
from p1_runner.ntrip_client import NTRIPClient
from p1_test_automation.devices_config import (DeviceConfig, RelayConfig,
                                               open_data_source)
from p1_test_automation.relay_controller import RelayController

from .base_interfaces import HitlDeviceInterfaceBase
from .interface_utils import enable_imu_output

UPDATE_WAIT_TIME_SEC = 15
RESTART_TIMEOUT_SEC = 10
NTRIP_POSITION_UPDATE_INTERVAL = 60
NTRIP_CONNECTION_TIMEOUT = 2

logger = logging.getLogger('point_one.hitl.lg69t_interface')

CORRECTIONS_URL = 'https://polaris.pointonenav.com:2102'
MOUNT_POINT = 'POLARIS'
NRIP_VERSION = 2


class NTRIPPositionUpdater:
    def __init__(self) -> None:
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()

    def stop_and_join(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join()
            self.thread = None

    def start(self, client: NTRIPClient, position: tuple[float, float, float]):
        def _run_loop(stop_event: threading.Event, client: NTRIPClient, position: tuple[float, float, float]):
            # If the system has been running for longer than NTRIP_CONNECTION_TIMEOUT and the NTRIP is disconnected
            # print a single warning. This resets if the client connects.
            start_time = time.monotonic()
            sent_warning = False
            while not stop_event.is_set():
                if client.connected:
                    client.send_position(position)
                    stop_event.wait(NTRIP_POSITION_UPDATE_INTERVAL)
                    sent_warning = False
                else:
                    if not sent_warning and time.monotonic() - start_time > NTRIP_CONNECTION_TIMEOUT:
                        logger.warning(f'NTRIP Client not connected.')
                        sent_warning = True
                    time.sleep(0.1)

        self.is_running = True
        self.thread = threading.Thread(target=_run_loop, args=(self.stop_event, client, position))
        self.thread.start()


class HitlLG69TInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        fields = ['JENKINS_UART1', 'JENKINS_UART2', 'JENKINS_RESET_RELAY']
        if not args.HITL_BUILD_TYPE.is_gnss_only():
            fields.append('JENKINS_COARSE_ORIENTATION')
        if not args.check_fields(fields):
            return None
        else:
            assert args.JENKINS_RESET_RELAY is not None  # For type check.
            return DeviceConfig(name=args.HITL_NAME,
                                serial_port=args.JENKINS_UART2,
                                reset_relay=RelayConfig(
                                    id=args.JENKINS_RESET_RELAY[0],
                                    relay_number=args.JENKINS_RESET_RELAY[1]
                                ))

    def __init__(self, config: DeviceConfig, env_args: HitlEnvArgs):
        self.config = config
        self.device_interface: Optional[DeviceInterface] = None
        self.corrections_client: Optional[NTRIPClient] = None
        self.reference_position_lla = env_args.JENKINS_ANTENNA_LOCATION
        self.env_args = env_args
        self.position_updater = NTRIPPositionUpdater()

    def init_device(self, build_info: Dict[str, Any], skip_reset=False,
                    skip_corrections=False) -> Optional[DeviceInterface]:
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

        if not skip_corrections:
            username = self.config.name
            password = os.getenv('HITL_POLARIS_API_KEY')
            if password is None:
                logger.error('No HITL_POLARIS_API_KEY key specified in environment.')
                return None
            if self.reference_position_lla is None:
                logger.error('No JENKINS_ANTENNA_LOCATION key specified in environment.')
                return None

        ################# Step 1: Update LG69T #####################

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
            # SysExits on failure
            applied_update = run_update(args)
            if applied_update:
                time.sleep(UPDATE_WAIT_TIME_SEC)

        data_source = open_data_source(self.config)
        if data_source is None:
            logger.error(f"Can't open Quectel serial interface.")
            return None
        self.device_interface = DeviceInterface(data_source)

        ################# Step 2: Factory Reset #####################

        if not skip_reset:
            # NOTE: This triggers a reboot which marks the start of the run.
            logger.info('Sending factory reset.')
            args = Namespace(type=['factory'])
            if not request_reset(self.device_interface, args):
                logger.error('Factory reset failed.')
                return None
            # Wait for reboot to finish. Prints error on failure.
            if not self.device_interface.wait_for_reboot(RESTART_TIMEOUT_SEC):
                return None

        ################# Step 3: Restore settings #####################

        # To test IMU data, set the coarse orientation (c_ds) and enable the IMUOutput message on the diagnostic port.
        # NOTE: This will leave unsaved UserConfig changes on the device. This also results in the c_ds changing shortly
        # after the start of the log. This may interfere with playback if this is not handled correctly. We do this
        # instead of saving the settings and doing an additional restart to reduce the number of flash writes.
        if not self.env_args.HITL_BUILD_TYPE.is_gnss_only():
            if not enable_imu_output(self.device_interface,
                                     self.env_args.JENKINS_COARSE_ORIENTATION, save=False):  # type: ignore
                logger.error('Setting up IMU orientation and output failed.')
                return None

        if not skip_corrections:
            def _on_corrections(self: HitlLG69TInterface, data: bytes):
                if self.device_interface is not None:
                    self.device_interface.data_source.write(data)

            self.corrections_client = NTRIPClient(url=CORRECTIONS_URL, mountpoint=MOUNT_POINT, username=username,
                                                  password=password,
                                                  data_callback=lambda data: _on_corrections(self, data),
                                                  version=NRIP_VERSION)
            self.corrections_client.start()
            assert self.reference_position_lla is not None
            self.position_updater.start(self.corrections_client, self.reference_position_lla)

        return self.device_interface

    def shutdown_device(self, tests_passed: bool, output_dir: Path) -> bool:
        logger.info('Stopping corrections updater.')
        self.position_updater.stop_and_join()
        logger.info('Stopping corrections client.')
        if self.corrections_client is not None:
            self.corrections_client.stop()
            self.corrections_client.join()

        logger.info('Stopping serial thread.')
        if self.device_interface:
            self.device_interface.data_source.stop()

        return True
