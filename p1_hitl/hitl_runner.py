#!/usr/bin/env python
import json
import logging
import os
import sys
from pathlib import Path

from fusion_engine_client.utils.log import find_log_file

# isort: split

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import (BUILD_INFO_FILE, CONSOLE_FILE, ENV_DUMP_FILE,
                          FULL_REPORT, LOG_FILES, PLAYBACK_DIR, DeviceType,
                          HitlEnvArgs, TestType, get_args)
from p1_hitl.device_interfaces import HitlAtlasInterface
from p1_hitl.get_build_artifacts import get_build_info
from p1_hitl.git_cmds import GitWrapper
from p1_hitl.jenkins_ctrl import run_build
from p1_hitl.metric_analysis.analysis_runner import (run_analysis,
                                                     run_analysis_playback)
from p1_hitl.metric_analysis.config_test import run_tests as run_config_tests
from p1_hitl.metric_analysis.metrics import MetricController
from p1_hitl.version_helper import git_describe_dut_version
from p1_runner.log_manager import LogManager

logger = logging.getLogger('point_one.hitl.runner')


def main():
    cli_args, env_args = get_args()
    if env_args is None:
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout
    )
    if cli_args.verbose > 0:
        logger.setLevel(logging.DEBUG)
        logging.getLogger('point_one.hitl').setLevel(logging.DEBUG)
        if cli_args.verbose > 1:
            logging.getLogger().setLevel(logging.DEBUG)

    logger.info(env_args)

    host_tools_commit = 'Unknown'
    try:
        git = GitWrapper(repo_root)
        host_tools_commit = git.describe(always=True)
        logger.info(f'p1-host-tools git commit: "{host_tools_commit}"')
        MetricController.analysis_commit = host_tools_commit
    except RuntimeError as e:
        logger.warning(f'Unable to git describe p1-host-tools repo: {e}')

    ################# Setup log directory #################
    log_manager = None
    if cli_args.playback_log:
        # Find playback file
        try:
            playback_file = find_log_file(
                cli_args.playback_log,
                log_base_dir=cli_args.logs_base_dir,
                candidate_files=[
                    'input.raw',
                    'input.p1bin'])
        except FileNotFoundError as e:
            logger.error(f'Playback log {cli_args.playback_log} found.')
            sys.exit(1)
        # Directory to store output files
        output_dir = Path(playback_file).parent / PLAYBACK_DIR
        os.makedirs(output_dir, exist_ok=True)
        # Clear any previous contents
        for file in os.listdir(output_dir):
            if file != CONSOLE_FILE:
                (output_dir / file).unlink()
    else:
        log_manager = LogManager(env_args.HITL_NAME,
                                 device_type=env_args.HITL_BUILD_TYPE.name,
                                 logs_base_dir=cli_args.logs_base_dir,
                                 directory_to_reuse=cli_args.reuse_log_dir,
                                 files=LOG_FILES)
        log_manager.create_log_dir()
        output_dir = Path(log_manager.get_log_directory())  # type: ignore

    env_file_dump = output_dir / ENV_DUMP_FILE
    HitlEnvArgs.dump_env_to_json_file(env_file_dump)
    MetricController.set_root_code_location(repo_root)
    MetricController.apply_environment_config_customizations(env_args)

    if cli_args.list_metric_only:
        MetricController.enable_logging(output_dir, False, False)
        MetricController.generate_report()
        print(open(output_dir / FULL_REPORT, 'r').read())
        sys.exit(0)

    ################# Get build to provision device under test #################
    if not cli_args.playback_log:
        release_str_build_type = DeviceType.get_build_type_from_version(env_args.HITL_DUT_VERSION)
        git_commitish = None
        if release_str_build_type is None:
            logger.info(
                f"HITL_DUT_VERSION '{env_args.HITL_DUT_VERSION}' is not a known version string. Assuming it's a git commitish")
            git_commitish = env_args.HITL_DUT_VERSION
            release_str = git_describe_dut_version(env_args)
            if release_str is None:
                logger.error(
                    f'HITL_DUT_VERSION "{env_args.HITL_DUT_VERSION}" is not a valid version string or git commitish.'
                    ' Cannot determine build to load.')
                sys.exit(1)
            logger.info(f"{release_str} is the release string for git commitish {git_commitish}")
        else:
            logger.info(
                f'HITL_DUT_VERSION "{env_args.HITL_DUT_VERSION}" is being interpreted as version string for'
                f' {release_str_build_type.name}')
            if release_str_build_type != env_args.HITL_BUILD_TYPE:
                logger.error(
                    f'DeviceType {release_str_build_type} inferred from HITL_DUT_VERSION {env_args.HITL_DUT_VERSION}'
                    f' does not match HITL_BUILD_TYPE {env_args.HITL_BUILD_TYPE.name}.')
                sys.exit(1)
            else:
                release_str = env_args.HITL_DUT_VERSION

        build_info = get_build_info(release_str, env_args.HITL_BUILD_TYPE)
        if build_info:
            logger.info(f'Build found: {build_info}')
            with open(output_dir / BUILD_INFO_FILE, 'w') as fd:
                json.dump(build_info, fd)
        else:
            if git_commitish is not None:
                # NOTE: Return error code 10 to indicate that the test was aborted. This will indicate that this run
                # shouldn't trigger an error report. This is done since Jenkins will sometimes either not trigger the
                # expected build commit, or the API may give repeated 500 errors for an unknown reason.
                if not run_build(git_commitish, env_args.HITL_BUILD_TYPE):
                    sys.exit(10)
                build_info = get_build_info(release_str, env_args.HITL_BUILD_TYPE)
                if build_info is None:
                    logger.error(
                        'Build artifacts still missing after successful Jenkins build. This may occur if several merges'
                        ' occurred in rapid succession and mapping of the branch to a release changed.')
                    sys.exit(10)
            else:
                logger.error(
                    f'HITL_DUT_VERSION {release_str} not found in build artifacts. Generate build artifacts, or rerun'
                    ' HITL with corresponding git commit to kick off build.')
                sys.exit(1)

    ################# Setup device under test #################
        if env_args.HITL_BUILD_TYPE == DeviceType.ATLAS:
            hitl_device_interface_cls = HitlAtlasInterface
        else:
            raise NotImplementedError('Need to handle other build types.')

        device_config = hitl_device_interface_cls.get_device_config(env_args)
        if device_config is None:
            logger.error('Failure configuring device for HITL testing.')
            sys.exit(1)
        hitl_device_interface = hitl_device_interface_cls(device_config)
        device_interface = hitl_device_interface.init_device(build_info, cli_args.skip_reset)
        if device_interface is None:
            logger.error('Failure initializing device for HITL testing.')
            sys.exit(1)

    ################# Run tests #################
    tests_completed = False
    tests_passed = False
    MetricController.enable_logging(output_dir, cli_args.playback_log, cli_args.log_metric_values)
    if cli_args.playback_log:
        MetricController.playback_host_time(output_dir.parent)

    try:
        if env_args.get_selected_test_type() == TestType.CONFIGURATION:
            if cli_args.playback_log:
                logger.error(f'HITL_TEST_TYPE "CONFIGURATION" does not support playback.')
                sys.exit(1)
            if log_manager is None:
                logger.error(f'HITL_TEST_TYPE "CONFIGURATION" expects active LogManager.')
                sys.exit(1)
            # The config test exercises starting the data source as part of its test.
            device_interface.data_source.stop()
            tests_completed = run_config_tests(env_args, device_config, log_manager)
        else:
            if cli_args.playback_log:
                tests_completed = run_analysis_playback(playback_file, env_args)
            else:
                if log_manager is not None:
                    log_manager.start()
                    device_interface.data_source.rx_log = log_manager  # type: ignore
                tests_completed = run_analysis(
                    device_interface,
                    env_args,
                    release_str)
        if tests_completed:
            MetricController.finalize()
            report = MetricController.generate_report()
            tests_passed = not report['has_failures']
    finally:
        try:
            hitl_device_interface.shutdown_device(tests_passed, output_dir)
        except:
            pass
        if log_manager is not None:
            log_manager.stop()

    if not tests_completed:
        sys.exit(1)

    sys.exit(0)


if __name__ == '__main__':
    main()
