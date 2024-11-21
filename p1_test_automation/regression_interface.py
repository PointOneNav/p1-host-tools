import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from p1_hitl.defs import DeviceType, HitlEnvArgs, TestType

logger = logging.getLogger('point_one.test_automation.regression_interface')

_HITL_API_URL = 'https://regression-api-rest.monitoring.pointonenav.com/v1/regression/insertHITLTest'
_DRIVE_API_URL = 'https://regression-api-rest.monitoring.pointonenav.com/v1/regression/insertDriveTest'


def report_to_regression_db(data, url: str) -> bool:
    auth = os.environ.get('HITL_REGRESSION_AUTH')
    if auth is None:
        logger.error('Authentication token not found in HITL_REGRESSION_AUTH environment variable.')
        return False

    logger.debug(f'POST {url}: {data}')

    headers = {"authorization": auth, "Content-Type": "text/plain"}

    try:
        r = requests.post(url, json=data, headers=headers)
        r.raise_for_status()
        success = False if r.json() is None else r.json().get('success', False)
        if success is False:
            logger.error(f'Unexpected response: {r.json()}')
        return success
    except Exception as e:
        logger.error(f"regression API exception {e}.")
        return False

    return True


def get_common_data(env_args: HitlEnvArgs, build_info: Dict[str, str],
                    success: bool, artifact: str) -> Optional[Dict[str, str | bool]]:
    git_hash = build_info.get('git_hash')
    if git_hash is None:
        logger.error('No git_hash in build_info.')
        return None
    tag = build_info.get('version', '')
    return {
        "build_type": env_args.HITL_BUILD_TYPE.name,
        "scenario": env_args.get_selected_test_type().name,
        "tag": tag,
        "commit_hash": git_hash,
        "success": success,
        "artifact": artifact,
    }


def report_hitl_result(env_args: HitlEnvArgs,
                       build_info: Dict[str, Any], success: bool, artifact: str, report: Optional[Path] = None) -> bool:
    data = get_common_data(env_args, build_info, success, artifact)
    if data is None:
        return False

    if report is not None and report.exists():
        data['report'] = open(report, 'r').read()
    return report_to_regression_db(data, _HITL_API_URL)


def report_drive_result(env_args: HitlEnvArgs,
                        build_info: Dict[str, Any], artifact: str, description: str, success: bool) -> bool:
    data = get_common_data(env_args, build_info, success, artifact)
    if data is None:
        return False
    data['description'] = description
    return report_to_regression_db(data, _DRIVE_API_URL)


def _main():
    logging.basicConfig(level=logging.DEBUG, format='%(message)s')
    env_args = HitlEnvArgs(
        HITL_NAME='dummy',
        HITL_DUT_VERSION='atlas_v2.2',
        HITL_BUILD_TYPE=DeviceType.LG69T_AM,
        HITL_NAUTILUS_PATH='',
        HITL_TEST_TYPE=TestType.SANITY
    )
    build_info = {
        "version": "atlas_v2.2",
        "git_hash": "e406dc4d0396c91805c3d6e8619aa7c969bf3029"
    }
    report_hitl_result(env_args, build_info, success=True, artifact='http://dummy-bucket.com/dummy-log')
    report_drive_result(
        env_args,
        build_info,
        artifact='http://dummy-bucket.com/dummy-log',
        description="[Novatel failure2]",
        success=True)


if __name__ == '__main__':
    _main()
