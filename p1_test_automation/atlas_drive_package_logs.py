#!/usr/bin/env python3

import json
import os
import shutil
import sys
from datetime import datetime

# isort: split

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# Put imports that rely on this in their own indent block to avoid linter reordering.
# isort: split
from p1_runner import trace as logging
from p1_test_automation.atlas_drive_constants import (
    DRIVE_DIR,
    DRIVE_TEST_TMP_DIR,
    LOCK_FILE,
    METADATA_FILENAME,
    TCP_DUMP_FILENAME,
)
from p1_test_automation.devices_config import load_config_set_dict

logger = logging.getLogger('point_one.test_automation.atlas_drive_package_logs')


def main():
    '''!
    Copy local files being logged from the temporary directory to the log directory to await upload.

    NOTE: The logs captured on the Atlases are expected to remain there until the uploaded script is called.
    '''
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)

    stop_time = int(datetime.now().timestamp())

    try:
        with open(LOCK_FILE) as fd:
            metadata = json.load(fd)
        config = load_config_set_dict(metadata['devices_config'])

        if len(config.devices) != len(metadata['drive_logs']):
            logger.error("Length of logs doesn't match number of devices.")
            exit(1)
    except:
        logger.error("Can't load drive metadata.")
        exit(1)

    metadata['stop_time'] = stop_time

    for device_index, device_config in enumerate(config.devices):
        metadata['device_index'] = device_index
        log_uid = metadata['drive_logs'][device_index]
        log_dir = DRIVE_DIR / log_uid
        os.makedirs(log_dir, exist_ok=True)

        tmp_pcap_filename = f'{device_index}_{device_config.name}_tcpdump.pcap'
        shutil.move(DRIVE_TEST_TMP_DIR / tmp_pcap_filename, log_dir / TCP_DUMP_FILENAME)

        with open(log_dir / METADATA_FILENAME, 'w') as fd:
            json.dump(metadata, fd, indent=2)


if __name__ == "__main__":
    main()
