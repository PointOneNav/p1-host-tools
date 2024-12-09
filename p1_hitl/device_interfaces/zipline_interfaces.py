import io
import logging
import os
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, Dict, Optional

import paramiko
import psutil
from scp import SCPClient

from bin.config_tool import apply_config, request_shutdown, save_config
from p1_hitl.defs import UPLOADED_LOG_LIST_FILE, HitlEnvArgs
from p1_hitl.get_build_artifacts import download_file
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.devices_config import DeviceConfig, open_data_source

from .base_interfaces import HitlDeviceInterfaceBase

RESTART_WAIT_TIME_SEC = 1
OUTPUT_PORT = 30200
DIAGNOSTIC_PORT = 30201

SSH_USERNAME = "pointone"
SSH_KEY_PATH = "/home/pointone/.ssh/id_ed25519"

logger = logging.getLogger('point_one.hitl.zipline_interface')


class HitlZiplineInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_LAN_IP']):
            return None
        else:
            # Interface is TCP3, which is configured as the diagnostic port.
            return DeviceConfig(name=args.HITL_NAME,
                                tcp_address=args.JENKINS_LAN_IP,
                                port=DIAGNOSTIC_PORT
                                )

    def __init__(self, config: DeviceConfig, env_args: HitlEnvArgs):
        self.config = config
        self.device_interface: Optional[DeviceInterface] = None
        self.ssh_client = None

    def init_device(self, build_info: Dict[str, Any], skip_reset=False,
                    skip_corrections=False) -> Optional[DeviceInterface]:
        # build_info example:
        # {
        #     "timestamp": 1725918926,
        #     "version": "v2.1.0-917-g7e74d1b235",
        #     "git_hash": "7e74d1b2356165d0e4408aa665ebf214e8a6dcb3",
        #     "aws_path": "s3://pointone-build-artifacts/nautilus/quectel/v2.1.0-917-g7e74d1b235/"
        # }

        logger.info(f'Initializing Zipline.')

        if self.config.tcp_address is None:
            raise KeyError('Config missing TCP address.')

        if skip_corrections:
            polaris_api_key = None
        else:
            polaris_api_key = os.getenv('HITL_POLARIS_API_KEY')

        pkey = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)

        # Set up SSH automation tool.
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.info(f'Attempting to connect to TCP address {self.config.tcp_address}')
        try:
            self.ssh_client.connect(self.config.tcp_address, username=SSH_USERNAME, pkey=pkey)
        except Exception as e:
            logger.error("Failed to connect to TCP address %s: %s" % (self.config.tcp_address, str(e)))
            return None

        # Check for successfull connection.
        transport = self.ssh_client.get_transport()
        if transport is None or not transport.is_active():
            logger.error('Failed to connect to TCP address.')
            return None

        # Clear all files from previous runs.
        # This should affectively "cold start" the runner.
        self.ssh_client.exec_command("rm -rf p1_fusion_engine*")

        # Download release from S3.
        aws_path = build_info["aws_path"]
        version_str = build_info["version"]
        tar_filename = "p1_fusion_engine-%s-zipline.tar.gz" % (version_str[8:])

        fd = io.BytesIO()

        if not download_file(fd, aws_path, tar_filename):
            logger.error("Failed to download file %s from %s" % (tar_filename, aws_path))
            return None

        fd.seek(0)
        scp = SCPClient(self.ssh_client.get_transport())
        scp.putfo(fd, f'/home/pointone/{tar_filename}')

        # Unzip the tar file.
        _stdin, _stdout, _stderr = self.ssh_client.exec_command(f"tar -xzf {tar_filename}")
        # Wait for exit status to ensure that tar command finished executing.
        exit_status = _stdout.channel.recv_exit_status()

        # Run bootstrap script.
        channel = transport.open_session()
        logger.info('Starting engine.')
        channel.exec_command(f"""
./p1_fusion_engine/run_fusion_engine.sh --lg69t-device /dev/zipline-lg69t \
--device-id hitl --cache ./p1_fusion_engine/cache --tcp-output-port {OUTPUT_PORT} \
--tcp-diagnostics-port {DIAGNOSTIC_PORT} --corrections-source polaris --polaris {polaris_api_key}""")
        # Manually wait to ensure that the bootstrap script kicks off in the background before continuing.
        time.sleep(RESTART_WAIT_TIME_SEC)

        # See if bootstrap script exited early.
        if channel.exit_status_ready():
            logger.error('Fusion engine process exited prematurely.')
            return None

        # Need to set up a DeviceInterface object that can be used to connect to the Pi.
        data_source = open_data_source(self.config)
        if data_source is None:
            logger.error('Failed to open data source.')
            return None

        self.device_interface = DeviceInterface(data_source)
        return self.device_interface

    def shutdown_device(self, tests_passed: bool, output_dir: Path) -> Optional[DeviceInterface]:
        if self.config.tcp_address is None or self.device_interface is None:
            return

        namespace_args = Namespace()
        namespace_args.type = 'log'

        if self.device_interface is not None:
            request_shutdown(self.device_interface, namespace_args)
            self.device_interface.data_source.stop()

        if self.ssh_client is not None:
            self.ssh_client.close()
