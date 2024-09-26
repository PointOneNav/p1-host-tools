import logging
import os
from typing import Dict

from jenkinsapi.jenkins import Jenkins
from p1_hitl.defs import BuildType

logger = logging.getLogger('point_one.hitl.jenkins_ctrl')

JENKINS_BASE_URL = 'https://build.pointonenav.com'

BUILD_JOB_MAP = {
    BuildType.ATLAS: "atlas-build-st-develop",
    BuildType.LG69T_AM: "quectel-build",
}

QUETEL_BUILD_TYPE_MAP = {
    BuildType.LG69T_AM: "gnss",
}


def _get_build_params(git_commitish: str, build_type: BuildType) -> Dict[str, str]:
    params = {'BRANCH': git_commitish}
    if build_type in list(QUETEL_BUILD_TYPE_MAP.keys()):
        params['BUILD_TYPE'] = QUETEL_BUILD_TYPE_MAP[build_type]
    return params


def run_build(git_commitish: str, build_type: BuildType) -> bool:
    JENKINS_API_USERNAME = os.environ.get('JENKINS_API_USERNAME')
    JENKINS_API_TOKEN = os.environ.get('JENKINS_API_TOKEN')

    if JENKINS_API_USERNAME is None or JENKINS_API_TOKEN is None:
        logger.error(f'Must set environment variables JENKINS_API_USERNAME and JENKINS_API_TOKEN to run Jenkins builds.')
        return False

    jenkins = Jenkins(JENKINS_BASE_URL, username=JENKINS_API_USERNAME, password=JENKINS_API_TOKEN)

    # This will start the job and will return a QueueItem object which
    # can be used to get build results
    job_name = BUILD_JOB_MAP[build_type]
    logger.info(f'Running {job_name} Jenkins build')
    job = jenkins[job_name]
    qi = job.invoke(build_params=_get_build_params(git_commitish, build_type))

    # Block until build is active
    if qi.is_queued():
        qi.block_until_building()

    build = qi.get_build()
    logger.info(f'Build {build} started, see: {build.get_build_url()}')

    # Block this script until build is finished
    if qi.is_queued() or qi.is_running():
        qi.block_until_complete()

    if not build.is_good():
        logger.warning(f'Build failed, see {build.get_build_url()}')
        return True
    else:
        return False


def _main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    run_build("origin/internal-jenkins-control", BuildType.ATLAS)


if __name__ == '__main__':
    _main()
