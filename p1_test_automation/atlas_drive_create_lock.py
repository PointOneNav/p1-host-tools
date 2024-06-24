#!/usr/bin/env python3

from datetime import datetime
import json
import os
import sys

# isort: split

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# Put imports that rely on this in their own indent block to avoid linter reordering.
# isort: split
from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser
from p1_test_automation.atlas_device_ctrl import get_log_started_at
from p1_test_automation.atlas_drive_constants import create_dirs, LOCK_FILE
from p1_test_automation.devices_config import load_config_set, load_json_with_comments

logger = logging.getLogger('point_one.test_automation.record-log-uuids')

def main():
    '''!
    Validate the devices are logging as expected, and capture metadata for the test as a "lock" file
    to indicate a test is in progress.
    '''
    parser = ArgumentParser()
    parser.add_argument(
        '-d',
        '--device-configurations',
        required=True,
        help="A JSON file with the configuration for the devices to display.",
    )
    parser.add_argument('--test-repo-git-commit', required=True, help="A git commit for the test repository version.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)

    # Create directories used by drive test if needed.
    create_dirs()

    start_time = int(datetime.now().timestamp())

    config = load_config_set(args.device_configurations)

    # Load the JSON config for drive to store in metadata.
    config_dict = load_json_with_comments(args.device_configurations)
    metadata = {
        'start_time': start_time,
        'test_repo_commit': args.test_repo_git_commit,
        'devices_config': config_dict,
        'drive_logs': [],
    }

    if config.truth:
        truth_log_guid = get_log_started_at(config.truth.tcp_address, start_time)
        if truth_log_guid is None:
            logger.error(f'Logging did not appear to start on truth reference.')
            exit(1)
        else:
            logger.info(f'Reference log started: {truth_log_guid}.')
            metadata['drive_reference_log'] = truth_log_guid

    for device_config in config.devices:
        log_guid = get_log_started_at(device_config.tcp_address, start_time)
        if log_guid is None:
            logger.error("Logging did not appear to start on device %s." % device_config.name)
            exit(1)

        logger.info(f'{device_config.name} log started: {log_guid}.')
        metadata['drive_logs'].append(log_guid)

    with open(LOCK_FILE, 'w') as fd:
        json.dump(metadata, fd, indent=2)

if __name__ == "__main__":
    main()
