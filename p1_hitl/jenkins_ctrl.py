import logging
import os
import time
import traceback
from typing import Dict

import requests
from jenkinsapi.jenkins import Jenkins

from p1_hitl.defs import DeviceType

logger = logging.getLogger('point_one.hitl.jenkins_ctrl')

JENKINS_BASE_URL = 'https://build.pointonenav.com'

BUILD_JOB_MAP = {
    DeviceType.AMAZON_FLEETEDGE_V1: "amazon-fe1-build",
    DeviceType.ATLAS: "atlas-build-st-develop",
    DeviceType.BMW_MOTO_MIC: "bmw-moto-build",
    DeviceType.P1_LG69T_GNSS: "p1-lg69t-gnss-build",
    DeviceType.LG69T_AM: "quectel-build",
    DeviceType.LG69T_AP: "quectel-build",
    DeviceType.ZIPLINE: "zipline-build",
}

# NOTE: Specifying "ins", or "gnss" doesn't appear to work correctly for the LG69T release job.
QUECTEL_BUILD_TYPE_MAP = {
    DeviceType.LG69T_AM: "all",
    DeviceType.LG69T_AP: "all",
}

NUM_OLD_BUILDS_TO_CHECK = 10
CONNECTION_ATTEMPTS = 10
RETRY_DELAY_SEC = 2


def _get_build_params(git_commitish: str, build_type: DeviceType) -> Dict[str, str]:
    params = {'BRANCH': git_commitish}
    if build_type in list(QUECTEL_BUILD_TYPE_MAP.keys()):
        params['BUILD_TYPE'] = QUECTEL_BUILD_TYPE_MAP[build_type]
    return params


def run_build(git_commitish: str, build_type: DeviceType) -> bool:
    JENKINS_API_USERNAME = os.environ.get('JENKINS_API_USERNAME')
    JENKINS_API_TOKEN = os.environ.get('JENKINS_API_TOKEN')

    if JENKINS_API_USERNAME is None or JENKINS_API_TOKEN is None:
        logger.error(f'Must set environment variables JENKINS_API_USERNAME and JENKINS_API_TOKEN to run Jenkins builds.')
        return False

    try:
        # Sometimes see spurious error: 403 Client Error
        jenkins = None
        for _ in range(CONNECTION_ATTEMPTS):
            try:
                jenkins = Jenkins(JENKINS_BASE_URL, username=JENKINS_API_USERNAME, password=JENKINS_API_TOKEN)
                break
            except requests.exceptions.HTTPError as e:
                logger.error(f'Problem connecting to Jenkins: {e}')
                time.sleep(RETRY_DELAY_SEC)

        if jenkins is None:
            return False

        job_name = BUILD_JOB_MAP[build_type]
        params = _get_build_params(git_commitish, build_type)

        # Three possibilities:
        # 1. A desired build is in the Jenkins build queue
        # 2. A desired build is in progress
        # 3. A build must be kicked off

        tracked_queue_item = None
        tracked_build = None
        # See if the desired build is queued.
        for _, item in jenkins.get_queue().iteritems():
            if item.get_job_name() == job_name and item.get_parameters() == params:
                logger.info(f'Found matching {job_name} in Jenkins queue.')
                tracked_queue_item = item
                break

        # See if the desired build is running.
        if tracked_queue_item is None:
            job = jenkins[job_name]
            last_build_number = job.get_last_buildnumber()
            start_build_number = max(job.get_first_buildnumber(), last_build_number - NUM_OLD_BUILDS_TO_CHECK)
            for number in range(last_build_number, start_build_number, -1):
                build = job.get_build(number)
                if build.is_running() and build.get_params() == params:
                    logger.info(f'Found matching {job_name} in progress.')
                    tracked_build = build
                    break

            # No job entries with matching param in progress or queue. Start new build.
            if tracked_build is None:
                logger.info(f'Kicking off {job_name} build.')
                tracked_queue_item = job.invoke(build_params=params)

        # Block until build is active
        if tracked_queue_item is not None:
            tracked_queue_item.block_until_building()
            tracked_build = tracked_queue_item.get_build()
            logger.info(f'Build {tracked_build} started, see: {tracked_build.get_build_url()}')

        # This is just to fix Python type inference and should not be possible.
        if tracked_build is None:
            logger.error('Invalid Jenkins monitoring state.')
            return False

        # Block this script until build is finished
        tracked_build.block_until_complete()

        # For some reason need to explicitly call this to get the new status.
        tracked_build.poll()
        if not tracked_build.is_good():
            logger.warning(f'Build failed, see {tracked_build.get_build_url()}')
            return False
        else:
            logger.info('Build succeeded.')
            return True
    except Exception as e:
        logger.error(f'Problem running Jenkins build: {traceback.format_exc()}')
        return False


def _main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    run_build("origin/st-develop", DeviceType.ATLAS)


if __name__ == '__main__':
    _main()
