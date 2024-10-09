#!/usr/bin/env python
import json
import logging
import sys
from argparse import ArgumentParser
from pathlib import Path

# isort: split

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, find_log_file

from p1_hitl.defs import BuildType, HitlEnvArgs, TestType
from p1_hitl.device_interfaces import AtlasInterface
from p1_hitl.get_build_artifacts import get_build_info
from p1_hitl.jenkins_ctrl import run_build
from p1_hitl.metric_analysis.analysis_runner import (run_analysis,
                                                     run_analysis_playback)
from p1_hitl.version_helper import git_describe_dut_version
from p1_runner.log_manager import LogManager
from p1_test_automation.devices_config_test import (ConfigSet, InterfaceTests,
                                                    TestConfig)
from p1_test_automation.devices_config_test import \
    run_tests as run_config_tests

logger = logging.getLogger('point_one.hitl.runner')

# TODO:
# - Generate report from metrics
# - Add wrapper to generate report with failure and console logs on failures
# - Update configuration test to use metrics


ENV_DUMP_FILE = 'env.json'
BUILD_INFO_FILE = 'build-info.json'

def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase verbosity.",
    )
    parser.add_argument(
        '--log-metric-values', action='store_true',
        help="Generate CSV's for each metric in the output directory.")
    parser.add_argument(
        '--logs-base-dir', metavar='DIR', default=DEFAULT_LOG_BASE_DIR,
        help="The base directory containing FusionEngine logs to be searched and written to.")
    parser.add_argument(
        '-p', '--playback-log', type=Path,
        help="Rather then connect to a device, re-analyze a log instead.")
    parser.add_argument(
        '-e', '--env-file', type=Path,
        help="Rather then load args from environment, use a JSON file.")
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

    if args.env_file:
        env_args = HitlEnvArgs.load_env_json_file(args.env_file)
    else:
        env_args = HitlEnvArgs.get_env_args()
    if env_args is None:
        exit(1)
    logger.info(env_args)

    ################# Get build to provision device under test #################
    if not args.playback_log:
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
    if env_args.HITL_TEST_TYPE == TestType.CONFIGURATION:
        if args.playback_log:
            logger.error(f'HITL_TEST_TYPE "CONFIGURATION" does not support playback.')
            exit(1)
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
        # Create a log directory to write results to.
        log_manager = LogManager(env_args.HITL_NAME, logs_base_dir=args.logs_base_dir)
        log_manager.start()
        output_dir = Path(log_manager.get_log_directory())  # type: ignore
        env_file_dump = output_dir / ENV_DUMP_FILE
        HitlEnvArgs.dump_env_to_json_file(env_file_dump)
        with open(output_dir / BUILD_INFO_FILE, 'w') as fd:
            json.dump(build_info, fd)
        ran_successfully = False
        try:
            if args.playback_log:
                # Locate the input file and set the output directory.
                try:
                    input_path = find_log_file(str(args.playback_log), log_base_dir=str(
                        args.logs_base_dir), candidate_files=['input.raw', 'input.p1bin'])
                    ran_successfully = run_analysis_playback(input_path, env_args, output_dir, args.log_metric_values)
                except FileNotFoundError as e:
                    logger.error(str(e))
            else:
                device_interface.data_source.rx_log = log_manager  # type: ignore
                ran_successfully = run_analysis(device_interface, env_args, output_dir, args.log_metric_values)
        finally:
            if not args.playback_log:
                device_interface.data_source.stop()
            log_manager.stop()

        if not ran_successfully:
            exit(1)

    exit(0)


if __name__ == '__main__':
    main()
