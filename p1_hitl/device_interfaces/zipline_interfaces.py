import io
import logging
import os
import time
from argparse import Namespace
from pathlib import Path
from scp import SCPClient
from typing import Any, Dict, Optional

import paramiko

from bin.config_tool import apply_config, request_shutdown, save_config
from p1_hitl.defs import UPLOADED_LOG_LIST_FILE, HitlEnvArgs
from p1_hitl.get_build_artifacts import download_file
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.atlas_device_ctrl import (CrashLogAction,
                                                  get_log_status,
                                                  restart_application,
                                                  set_crash_log_action,
                                                  upload_log)
from p1_test_automation.devices_config import (BalenaConfig, DeviceConfig,
                                               open_data_source)

from .base_interfaces import HitlDeviceInterfaceBase

UPDATE_TIMEOUT_SEC = 60 * 20
UPDATE_POLL_INTERVAL_SEC = 10
UPDATE_WAIT_TIME_SEC = 60
RESTART_WAIT_TIME_SEC = 30

SSH_USERNAME = "pointone"
SSH_KEY_PATH = "/home/pointone/.ssh/id_ed25519"

logger = logging.getLogger('point_one.hitl.zipline_interface')

class HitlZiplineInterface(HitlDeviceInterfaceBase):
    @staticmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        if not args.check_fields(['JENKINS_LAN_IP']):
            return None
        else:
            return DeviceConfig(name=args.HITL_NAME,
                                tcp_address=args.JENKINS_LAN_IP,
                                )

    def __init__(self, config: DeviceConfig, env_args: HitlEnvArgs):
        self.old_log_guids = set()
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


        pkey = paramiko.Ed25519Key.from_private_key_file(SSH_KEY_PATH)

        # Set up SSH automation tool.
        self.ssh_client = paramiko.SSHClient()
        self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        logger.info(self.config.tcp_address)
        self.ssh_client.connect(self.config.tcp_address, username=SSH_USERNAME, pkey=pkey)

        # TODO: Add check to make sure the connection is successful.

        # Download release from S3.
        # TODO verify what the AWS path should look like. I'm expecting it to look exactly like: s3://pointone-build-artifacts/nautilus/zipline/v0.2.3/p1_fusion_engine-v0.2.3-zipline.tar.gz
        aws_path = build_info["aws_path"]
        version_str = build_info["version"]
        tar_filename = "p1_fusion_engine-%s-zipline.tar.gz" % (version_str[8:])
        logger.info(tar_filename)

        fd = io.BytesIO()

        if not download_file(fd, aws_path, tar_filename):
            logger.error("Failed to download file %s from %s" % (tar_filename, aws_path))

        # For now, save the tar ball locally
        with open(tar_filename, 'wb') as f:
            f.write(fd.getbuffer())

        scp = SCPClient(self.ssh_client.get_transport())
        scp.put(tar_filename, f'/home/pointone/{tar_filename}')

        # Unzip the tar file.
        _stdin, _stdout, _stderr = self.ssh_client.exec_command(f"tar -xzf {tar_filename}")
        # Wait for exit status to ensure that tar command finished executing.
        exit_status = _stdout.channel.recv_exit_status()

        # Run bootstrap script.
        transport = self.ssh_client.get_transport()
        channel = transport.open_session()
        channel.exec_command("./p1_fusion_engine/run_fusion_engine.sh --lg69t-device /dev/zipline-lg69t --device-id hitl --cache ./p1_fusion_engine/cache --tcp-output-port 30200 --tcp-diagnostics-port 30201")
        # Hack: use sleep command to ensure that bootstrap script kicks off in the background.
        time.sleep(1)

        # Need to set up a DeviceInterface object that can be used to connect to the Pi.
        data_source = open_data_source(self.config)
        logger.info("DATA SOURCE: ")
        logger.info(data_source)
        self.device_interface = DeviceInterface(data_source)
        return self.device_interface


    def shutdown_device(self, tests_passed: bool, output_dir: Path) -> Optional[DeviceInterface]:
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

        self.ssh_client.close()
