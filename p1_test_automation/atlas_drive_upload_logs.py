#!/usr/bin/env python3

import concurrent.futures
import json
import os
import shutil
import sys
import time
from glob import glob
from typing import List, Tuple

import boto3

# isort: split

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# Put imports that rely on this in their own indent block to avoid linter reordering.
# isort: split
from p1_runner import trace as logging
from p1_test_automation.atlas_device_ctrl import (get_log_key, get_log_status,
                                                  upload_log)
from p1_test_automation.atlas_drive_constants import (DRIVE_DIR,
                                                      METADATA_FILENAME,
                                                      TCP_DUMP_FILENAME)
from p1_test_automation.devices_config import (DeviceConfig,
                                               load_config_set_dict)

logger = logging.getLogger('point_one.test_automation.atlas_drive_upload_logs')

MAX_THREADS = 4

S3_DEFAULT_INGEST_BUCKET = 'pointone-ingest-landingpad'
S3_DEFAULT_REGION = 'us-west-1'


def meta_upload_func(log_uid: str, log_key: str):
    """!
    Function for parallelizing upload of local data to S3.
    """
    log_dir = DRIVE_DIR / log_uid
    logger.info(f'Uploading metadata to {log_key}')

    s3_client = boto3.client('s3', region_name=S3_DEFAULT_REGION)
    s3_client.upload_file(
        log_dir / TCP_DUMP_FILENAME, S3_DEFAULT_INGEST_BUCKET, os.path.join(log_key, TCP_DUMP_FILENAME)
    )
    s3_client.upload_file(
        log_dir / METADATA_FILENAME, S3_DEFAULT_INGEST_BUCKET, os.path.join(log_key, METADATA_FILENAME)
    )


def main():
    """!
    Look for completed drives and upload results to S3. This does the following:
    1. Upload the logs from the devices directly to S3.
    2. Upload locally collected data and metadata for each log to S3.

    Originally, there were some alternative approaches taken for this:
    1. Download all Atlas logs locally on drive stop and upload together. This meant needing to wait
       several minutes to "stop" the log.
    2. Upload the local results to the Atlas devices to use their upload functionality.

    In both these cases the concern was the it would require unnecessary transfers and disk space.
    While SCP could speed up some transfers, that adds a layer of authentication complexity.

    Failed uploads should be "non-destructive" with the results remaining where they are, and the
    test metadata being moved to a `*.failed` file for manual uploading of the results if desired.

    NOTE: Logs on the devices are not cleared automatically and need to be manually deleted if
          desired.
    """
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)

    paths = glob(os.path.join(DRIVE_DIR, 'drive_test_*.json'))
    if len(paths) == 0:
        logger.info('No logs to upload.')
        exit(0)

    for path in paths:
        logger.info(f'Uploading results from drive {path}.')
        load_failed = True
        try:
            with open(path) as fd:
                metadata = json.load(fd)
            config = load_config_set_dict(metadata['devices_config'])

            if len(config.devices) != len(metadata['drive_logs']):
                logger.error(f"Length of logs doesn't match number of devices: {path}.")
            else:
                load_failed = False
        except Exception as e:
            logger.error(f"Can't load drive metadata {path}: {e}.")

        if load_failed:
            fail_path = path + '.failed'
            logger.error(f"Moving drive description to {fail_path} for manual uploading.")
            shutil.move(path, fail_path)
            continue

        truth_upload_failed = False
        if 'drive_reference_log' in metadata and config.truth:
            truth_uuid = metadata['drive_reference_log']
            logger.info(f"Uploading truth log: {truth_uuid}")
            truth_upload_failed = not upload_log(config.truth.tcp_address, truth_uuid)

        # Start all device uploads.
        uploading_logs: Tuple[str, DeviceConfig] = []
        for log_uid, device_config in zip(metadata['drive_logs'], config.devices):
            logger.info(f"Uploading log {log_uid} for {device_config.name}")
            if upload_log(device_config.tcp_address, log_uid):
                uploading_logs.append((log_uid, device_config))

        remaining_logs = uploading_logs
        # Wait for uploads to finish.
        while True:
            new_remaining_logs = []
            for log_uid, device_config in remaining_logs:
                status = get_log_status(device_config.tcp_address)
                if status is None:
                    logger.error(
                        f"Device {device_config.name} failed to respond during upload. Need to manually verify log uploaded successfully."
                    )
                    pass
                elif status['in_progress'] and status['bytes_remaining'] != 0:
                    new_remaining_logs.append((log_uid, device_config))
                    logger.info(f"{log_uid}: {status['bytes_remaining']/1024/1024:.1f} MB remaining.")
            if len(new_remaining_logs) > 0:
                time.sleep(10)
                remaining_logs = new_remaining_logs
                logger.info(f"=========== Waiting for {len(remaining_logs)} logs ================")
            else:
                break

        # This could be run concurrently with the device log uploads, but since these often use the
        # same WAN connection, I didn't want to compete for bandwidth.
        #
        # Upload the local files to S3 on parallel threads.
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_THREADS) as executor:
            futures: List[concurrent.futures.Future] = []
            for log_uid, device_config in uploading_logs:
                log_key = get_log_key(device_config.tcp_address, log_uid)
                if log_key is not None:
                    futures.append(
                        executor.submit(
                            meta_upload_func,
                            log_uid=log_uid,
                            log_key=log_key,
                        )
                    )

        # Wait for uploads to complete.
        successful_logs = []
        for device_index, future in enumerate(futures):
            log_uid = uploading_logs[device_index][0]
            device_name = uploading_logs[device_index][1].name
            try:
                future.result()
                successful_logs.append(log_uid)
            except Exception as exc:
                logger.error(f'log {log_uid} for {device_name} generated an exception: {exc}')

        # If not all the uploads succeeded, mark the ones that did succeed as "DONE", and copy
        # metadata with the failed logs to `*.failed`.
        if truth_upload_failed or len(successful_logs) < len(metadata['drive_logs']):
            if not truth_upload_failed and 'drive_reference_log' in metadata:
                metadata['drive_reference_log'] = "DONE"

            for log in successful_logs:
                idx = metadata['drive_logs'].index(log)
                metadata['drive_logs'][idx] = "DONE"

            fail_path = path + '.failed'
            logger.error(f"Moving drive description for failed uploads to {fail_path} for manual uploading.")
            with open(fail_path, 'w') as fd:
                json.dump(metadata, fd, indent=2)
        else:
            logger.info('Upload completed successfully.')

        os.unlink(path)


if __name__ == "__main__":
    main()
