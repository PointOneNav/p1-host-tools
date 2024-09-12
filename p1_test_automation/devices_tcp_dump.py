#!/usr/bin/env python3

import os
import subprocess
import sys
import time
from typing import List

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

# isort: split
from p1_runner.argument_parser import ArgumentParser
from p1_test_automation.devices_config import load_config_set

DRIVE_TEST_TMP_DIR = '/tmp/atlas_drive_test'


def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-d',
        '--device-configurations',
        default=None,
        help="A JSON file with the configuration for the devices to display.",
    )
    args = parser.parse_args()

    config = load_config_set(args.device_configurations)

    processes: List[subprocess.Popen] = []
    for device_index, device_config in enumerate(config.devices):
        if device_config.tcp_address is not None:
            dump_filename = f"{device_index}_{device_config.name}_tcpdump.pcap"
            dump_path = os.path.join(DRIVE_TEST_TMP_DIR, dump_filename)
            processes.append(
                subprocess.Popen(
                    ["sudo", "tcpdump", "-w", dump_path, "-i", "any", "-nn", "src", device_config.tcp_address]
                )
            )

    try:
        while True:
            time.sleep(100)
    except KeyboardInterrupt:
        for process in processes:
            process.kill()


if __name__ == "__main__":
    main()
