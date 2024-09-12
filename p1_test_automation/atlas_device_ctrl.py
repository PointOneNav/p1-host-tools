#!/usr/bin/env python3

import logging
from enum import Enum, auto
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger('point_one.test_automation.atlas_device_ctrl')

TIMEOUT_SEC = 10


class LoggingCmd(Enum):
    START = auto()
    STOP = auto()


class CrashLogAction(Enum):
    NONE = auto()
    MANIFEST_ONLY = auto()
    FULL_LOG = auto()


def get_log_status(tcp_address: str) -> Optional[Dict[str, Any]]:
    """!
    @brief Query status of logs from Nemo REST API.

    @param tcp_address host address of device to query.

    @return A dict with the JSON response or `None` on query failure.
    """
    url = "http://{tcp_address}/api/v1/log/status".format(tcp_address=tcp_address)
    try:
        r = requests.get(url, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"get_log_status exception {e}.")
        return None

    return r.json()


def get_log_key(tcp_address: str, log_guid: str) -> Optional[str]:
    """!
    @brief Get the AWS S3 key the log would be uploaded to over the Nemo REST API.

    This is found by using the log path in the status. This path is made with the same function that
    turns `device_id` and `creation_time` into the S3 key.

    @param tcp_address host address of device to query.
    @param log_guid the log to get the key for.

    @return The log path or `None` on query failure.
    """
    status = get_log_status(tcp_address)
    if status:
        for log in status['logs']:
            if log['guid'] == log_guid:
                EXPECTED_START_PATH = '/data/logs/'
                if not log['path'].startswith(EXPECTED_START_PATH):
                    logger.error(
                        f'Log path does not start with expected "{EXPECTED_START_PATH}" instead was "{log["path"]}".'
                    )
                else:
                    return log['path'][len(EXPECTED_START_PATH):]

    return None


def download_log(tcp_address: str, log_guid: str, output_filename: str) -> Optional[str]:
    """!
    @brief Download a log on the device to a local file over the Nemo REST API.

    @param tcp_address host address of device to query.
    @param log_guid the log to download.
    @param output_filename the file to write the compressed log to.

    @return The log guid or `None` on query failure.
    """
    url = "http://{tcp_address}/api/v1/log/{log_guid}/download".format(tcp_address=tcp_address, log_guid=log_guid)
    try:
        r = requests.get(url, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"download_log exception {e}.")
        return None

    open(output_filename, 'wb').write(r.content)
    return log_guid


def upload_log(tcp_address: str, log_guid: str) -> bool:
    """!
    @brief Upload a log on the device to the S3 ingest bucket over the Nemo REST API.

    @param tcp_address host address of device to query.
    @param log_guid the log to upload.

    @return `true` on success and `false` on failure.
    """
    url = "http://{tcp_address}/api/v1/log/{log_guid}/upload".format(tcp_address=tcp_address, log_guid=log_guid)
    try:
        r = requests.post(url, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"upload_log exception {e}.")
        return False

    return True


def get_log_started_at(tcp_address: str, start_time: float, tolerance=120) -> Optional[str]:
    """!
    @brief Find the log on the device started closest to a given time over the Nemo REST API.

    @param tcp_address host address of device to query.
    @param start_time the approximate posix epoch second to the log started at.
    @param tolerance the time window in seconds for matching a log.

    @return the guid of the log started closest to `start_time` or `None` if no log was withing the
            `tolerance` of the specified time.
    """
    status = get_log_status(tcp_address)
    found_log = None
    if status is not None:
        min_dt = tolerance
        for log in status['logs']:
            dt = abs(start_time - log['start_time'])
            if dt < min_dt:
                found_log = log['guid']
                min_dt = dt

        if found_log is None:
            logger.warning(f"No log found on device within {tolerance} of {start_time} (closest off by {min_dt}).")

    return found_log


def download_log_started_at(tcp_address: str, start_time: float, output_filename: str, tolerance=100) -> Optional[str]:
    """!
    @brief Download a log on the device to a local file over the Nemo REST API.

    @param tcp_address host address of device to query.
    @param start_time the approximate posix epoch second to the log started at.
    @param output_filename the file to write the compressed log to.
    @param tolerance the time window in seconds for matching a log.

    @return The log guid or `None` on query or time match failure.
    """
    guid = get_log_started_at(tcp_address, start_time, tolerance)
    if guid is not None:
        return download_log(tcp_address, guid, output_filename)

    return None


def enable_rolling_logs(tcp_address: str, enabled: bool) -> bool:
    """!
    @brief Set the `enable_rolling_logs` setting over the Nemo REST API.

    @param tcp_address host address of device to set.
    @param enabled whether rolling logs should be enabled.

    @return `true` on success and `false` on failure.
    """
    logger.info(f"Enabling rolling logs: {enabled}.")
    url = f"http://{tcp_address}/api/v1/application/settings"
    data = {'application': {'logging': {'enable_rolling_logs': enabled}}}

    try:
        r = requests.post(url, json=data, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"enable_rolling_logs exception {e}.")
        return False

    logger.debug(r.json())

    return True


def set_crash_log_action(tcp_address: str, action: CrashLogAction) -> bool:
    """!
    @brief Set the `crash_log_upload` setting over the Nemo REST API.

    @param tcp_address host address of device to set.
    @param action action to take when crash log is detected.

    @return `true` on success and `false` on failure.
    """
    logger.debug(f"Crash log action: {action.name}.")
    url = f"http://{tcp_address}/api/v1/application/settings"
    data = {'application': {'crash_log_upload': action.name}}

    try:
        r = requests.post(url, json=data, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"set_crash_log_action exception {e}.")
        return False

    logger.debug(r.json())

    return True


def send_logging_cmd_to_legacy_atlas(tcp_address: str, cmd: LoggingCmd) -> bool:
    """!
    @brief For an Atlas running a legacy `develop` build start or stop a log over the Nemo REST API.

    @param tcp_address host address of device to set.
    @param cmd whether to start or stop.

    @return `true` on success and `false` on failure.
    """
    logger.info(f"Sending legacy Atlas {cmd.name} command.")
    # Starting versus stopping the device only requires a small difference in the URL.
    if cmd == LoggingCmd.START:
        url = "http://{tcp_address}/api/v1/application/start".format(tcp_address=tcp_address)
    else:
        url = "http://{tcp_address}/api/v1/application/stop".format(tcp_address=tcp_address)

    try:
        r = requests.post(url, timeout=TIMEOUT_SEC)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"send_logging_cmd_to_legacy_atlas exception {e}.")
        return False

    return True


def restart_application(tcp_address, log_on_startup=False) -> bool:
    """!
    @brief Restart the nautilus container on an Atlas running a st-develop build over the Nemo REST
           API.

    This is generally used to capture a "deterministic" log that can be replayed with high fidelity.

    @param tcp_address host address of device to set.
    @param log_on_startup whether to immediately start logging after the restart.

    @return `true` on success and `false` on failure.
    """
    logger.info(f"Restarting application. Logging: {log_on_startup}.")
    url = f"http://{tcp_address}/api/v1/application/deterministic_restart"
    data = {'log_on_startup': log_on_startup}

    try:
        # This command can take awhile.
        r = requests.post(url, json=data, timeout=TIMEOUT_SEC * 2)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"restart_application exception {e}.")
        return False

    return True
