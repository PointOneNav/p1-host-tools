#!/usr/bin/env python
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path

# Add the host tool root directory and device_init to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import BuildType, HitlEnvArgs, TestType
from p1_hitl.device_init import AtlasInit
from p1_hitl.get_build_artifacts import get_build_info
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
    if env_args.HITL_DUT_VERSION:
        build_type = BuildType.get_build_type_from_version(env_args.HITL_DUT_VERSION)
        if build_type is None:
            exit(1)
        elif build_type != env_args.HITL_BUILD_TYPE:
            logger.error(
                f'BuildType {build_type} inferred from HITL_DUT_VERSION {env_args.HITL_DUT_VERSION} does not match expected BuildType {env_args.HITL_BUILD_TYPE}.')
            exit(1)
        elif build_type == BuildType.ATLAS:
            device_init = AtlasInit()
        else:
            raise NotImplementedError('Need to handle other build types.')

        device_config = device_init.get_device_config(env_args)
        if device_config is None:
            logger.error('Failure configuring device for HITL testing.')
            exit(1)

        build_info = get_build_info(env_args.HITL_DUT_VERSION, build_type)
        if build_info:
            logger.info(f'Build found: {build_info}')
            device_interface = device_init.init_device(device_config, build_info)
            if device_interface is None:
                logger.error('Failure initializing device for HITL testing.')
                exit(1)
        else:
            logger.info('Need to run Build.')
    else:
        raise NotImplementedError('Need to handle only knowing HITL_BUILD_COMMIT and HITL_BUILD_TYPE')

    # TODO: Add actual testing metric processing.
    if env_args.HITL_TEST_TYPE == TestType.CONFIGURATION:
        # The config test exercises starting the data source as part of its test.
        device_interface.data_source.stop()
        interface_name = {BuildType.ATLAS: 'tcp1'}.get(build_type)
        test_set = ["fe_version", "interface_ids", "expected_storage", "msg_rates", "set_config",
                    "import_config", "save_config"]
        # TODO: Figure out what to do about Atlas reboot.
        if build_type != BuildType.ATLAS:
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
