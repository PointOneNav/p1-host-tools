#!/usr/bin/env python
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path

# isort: split

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import BuildType, HitlEnvArgs, TestType
from p1_hitl.device_interfaces import AtlasInterface
from p1_hitl.get_build_artifacts import get_build_info
from p1_hitl.jenkins_ctrl import run_build
from p1_hitl.version_helper import git_describe_dut_version
from p1_test_automation.devices_config_test import (ConfigSet, InterfaceTests,
                                                    TestConfig)
from p1_test_automation.devices_config_test import \
    run_tests as run_config_tests

logger = logging.getLogger('point_one.hitl.runner')


def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase verbosity.",
    )
    args = parser.parse_args()

    if args.verbose == 0:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    else:
        logging.basicConfig(
            level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout
        )
        logger.setLevel(logging.DEBUG)
        logging.getLogger('point_one.config_test').setLevel(logging.DEBUG)
        if args.verbose > 1:
            logging.getLogger().setLevel(logging.DEBUG)

    env_args = HitlEnvArgs.get_env_args()
    if env_args is None:
        exit(1)

    logger.info(env_args)

    ################# Get build to provision device under test #################
    release_str_build_type = BuildType.get_build_type_from_version(env_args.HITL_DUT_VERSION)
    git_commitish = None
    if release_str_build_type is None:
        logger.info(
            f"HITL_DUT_VERSION '{env_args.HITL_DUT_VERSION}' is not a known version string. Assuming it's a git commitish")
        git_commitish = env_args.HITL_DUT_VERSION
        release_str = git_describe_dut_version(env_args)
        if release_str is None:
            logger.error(
                f'HITL_DUT_VERSION "{env_args.HITL_DUT_VERSION}" is not a valid version string or git commitish. Cannot determine build to load.')
            exit(1)
        logger.info(f"{release_str} is the release string for git commitish {git_commitish}")
    else:
        logger.info(
            f'HITL_DUT_VERSION "{env_args.HITL_DUT_VERSION}" is being interpreted as version string for {release_str_build_type.name}')
        if release_str_build_type != env_args.HITL_BUILD_TYPE:
            logger.error(
                f'BuildType {release_str_build_type} inferred from HITL_DUT_VERSION {env_args.HITL_DUT_VERSION} does not match HITL_BUILD_TYPE {env_args.HITL_BUILD_TYPE.name}.')
            exit(1)
        else:
            release_str = env_args.HITL_DUT_VERSION

    build_info = get_build_info(release_str, env_args.HITL_BUILD_TYPE)
    if build_info:
        logger.info(f'Build found: {build_info}')
    else:
        if git_commitish is not None:
            if not run_build(git_commitish, env_args.HITL_BUILD_TYPE):
                exit(1)
            build_info = get_build_info(release_str, env_args.HITL_BUILD_TYPE)
            if build_info is None:
                logger.error(
                    f'Build artifacts still missing after successful Jenkins build. This may occur if several merges occurred in rapid succession and mapping of the branch to a release changed.')
                exit(1)
        else:
            logger.error(
                f'HITL_DUT_VERSION {release_str} not found in build artifacts. Generate build artifacts, or rerun HITL with corresponding git commit to kick off build.')
            exit(1)

    ################# Setup device under test #################
    if env_args.HITL_BUILD_TYPE == BuildType.ATLAS:
        device_interfaces = AtlasInterface()
    else:
        raise NotImplementedError('Need to handle other build types.')

    device_config = device_interfaces.get_device_config(env_args)
    if device_config is None:
        logger.error('Failure configuring device for HITL testing.')
        exit(1)
    device_interface = device_interfaces.init_device(device_config, build_info)
    if device_interface is None:
        logger.error('Failure initializing device for HITL testing.')
        exit(1)

    ################# Run tests #################
    # TODO: Add actual testing metric processing.
    if env_args.HITL_TEST_TYPE == TestType.CONFIGURATION:
        # The config test exercises starting the data source as part of its test.
        device_interface.data_source.stop()
        interface_name = {BuildType.ATLAS: 'tcp1'}.get(env_args.HITL_BUILD_TYPE)
        test_set = ["fe_version", "interface_ids", "expected_storage", "msg_rates", "set_config",
                    "import_config", "save_config"]
        # TODO: Figure out what to do about Atlas reboot.
        if env_args.HITL_BUILD_TYPE != BuildType.ATLAS:
            test_set += ["reboot", "watchdog_fault", "expected_storage"]
        test_config = TestConfig(
            config=ConfigSet(
                devices=[device_config]
            ),
            tests=[
                InterfaceTests(
                    name=device_config.name,
                    interface_name=interface_name,
                    tests=test_set
                )
            ])
        run_config_tests(test_config)
    else:
        raise NotImplementedError('Need to handle other HITL_TEST_TYPE values.')


if __name__ == '__main__':
    main()
