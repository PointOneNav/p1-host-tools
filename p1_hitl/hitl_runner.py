#!/usr/bin/env python
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path

# Add the host tool root directory and device_init to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import BuildType, HiltEnvArgs, TestType
from p1_hitl.device_init import AtlasInit
from p1_hitl.get_build_artifacts import get_build_info

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

    env_args = HiltEnvArgs.get_env_args()
    if env_args is None:
        exit(1)

    logger.info(env_args)
    if env_args.HITL_DUT_VERSION:
        build_type = BuildType.get_build_type_from_version(env_args.HITL_DUT_VERSION)
        if build_type is None:
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
        pass
    else:
        raise NotImplementedError('Need to handle other HITL_TEST_TYPE values.')


if __name__ == '__main__':
    main()
