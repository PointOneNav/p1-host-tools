#!/usr/bin/env python
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

# isort: split

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import (BUILD_INFO_FILE, CONSOLE_FILE, DEFAULT_LOG_BASE_DIR,
                          ENV_DUMP_FILE, FAILURE_REPORT, FULL_REPORT,
                          LOG_FILES, PLAYBACK_DIR, UPLOADED_LOG_LIST_FILE,
                          HitlEnvArgs, get_args)
from p1_hitl.hitl_slack_interface import FileEntry, send_slack_message
from p1_runner.log_manager import LogManager

# TODO: Slack integration.

# Give the process 40 minutes above the test duration for build and setup.
BUILD_AND_SETUP_TIMEOUT_SEC = 40 * 60
KILL_TIMEOUT_SEC = 10
UPDATE_INTERVAL_SEC = 1
RUNNER_SCRIPT_PATH = repo_root / 'p1_hitl/hitl_runner.py'
S3_DEFAULT_INGEST_BUCKET = 'pointone-ingest-landingpad'

logger = logging.getLogger('point_one.hitl.wrapper')


def report_failure(msg: str, env_args: Optional[HitlEnvArgs] = None, log_base_dir=DEFAULT_LOG_BASE_DIR,
                   log_dir: Path = Path()):
    logger.warning(msg)

    # Try to post to slack
    channel = os.getenv('HITL_SLACK_CHANNEL')
    token = os.getenv('HITL_SLACK_BOT_TOKEN')
    if channel is None or token is None:
        logger.warning(
            'Missing environment parameter "HITL_SLACK_CHANNEL" and/or "HITL_SLACK_BOT_TOKEN". Cannot post to slack.')
        return

    if env_args:
        slack_mrkdwn = f'''\
*HITL {env_args.get_selected_test_type().name} Test Failed*
Node: `{env_args.HITL_NAME}`
Platform Config: `{env_args.HITL_BUILD_TYPE.name}`
Software Version: `{env_args.HITL_DUT_VERSION}`
'''
    else:
        slack_mrkdwn = '*HITL TEST FAILED*'

    jenkins_build_url = os.getenv('BUILD_URL')
    if jenkins_build_url is not None:
        build_url_line = f'See <{jenkins_build_url}> for info on PR and full job log.'
    else:
        build_url_line = f'Job was run outside Jenkins.'

    slack_mrkdwn += f'''\
{build_url_line}

{msg}

'''
    files_to_attach = []
    if log_dir:
        log_directory = log_dir.relative_to(log_base_dir)
        slack_mrkdwn += f'''\
Console output, configuration, and data uploaded to:
<https://console.aws.amazon.com/s3/buckets/{S3_DEFAULT_INGEST_BUCKET}/{log_directory}/>

See attachments in reply for more details.
'''
        files_to_check = [
            FileEntry(log_dir / FAILURE_REPORT, "Description of failed metrics"),
            FileEntry(log_dir / CONSOLE_FILE, "HITL Runner Console"),
            FileEntry(log_dir / FULL_REPORT, "Full report with metric configurations and results"),
            FileEntry(
                log_dir / UPLOADED_LOG_LIST_FILE,
                "Additional uploaded device logs collected during run"),
            FileEntry(log_dir / ENV_DUMP_FILE, "The environment arguments for the run"),
            FileEntry(log_dir / BUILD_INFO_FILE, "Metadata for the build loaded on the device"),]
        files_to_attach = [f for f in files_to_check if f.file_path.exists()]

    send_slack_message(channel, token, slack_mrkdwn, files_to_attach)


def main():
    logging.basicConfig(level=logging.INFO, format='>>>> %(message)s', stream=sys.stdout)

    try:
        cli_args, env_args = get_args()
    except:
        env_args = None
    if env_args is None:
        report_failure('Problem evaluating arguments for running HITL. This likely reflects a problem in the Jenkins'
                       ' setup or the scripts calling this application.')
        sys.exit(1)

    try:
        cmd_args = sys.argv
        test_set = env_args.HITL_TEST_TYPE.get_test_set()
        is_multi_test_set = len(
            test_set) > 1 and env_args.HITL_TEST_SET_INDEX is None and cli_args.test_set_index is None
        if is_multi_test_set:
            logger.info(f'Starting multi-test set {(t.name for t in test_set)}.')

        env_args_dict = dict(env_args._asdict())
        # Iterate over tests that make up the test set. These will be totally independent HITL runner processes.
        for i, test_type in enumerate(test_set):
            extra_args = []
            # Setup log directory to capture full console output (this directory is passed to child process to keep
            # using).
            log_dir = Path()
            # There's no real reason to run playback through the wrapper, but don't create a log directory if it occurs.
            logger.info('Creating log directory.')
            if not cli_args.playback_log:
                log_manager = LogManager(
                    device_id=env_args.HITL_NAME,
                    device_type=env_args.HITL_BUILD_TYPE.name,
                    logs_base_dir=cli_args.logs_base_dir,
                    directory_to_reuse=cli_args.reuse_log_dir,
                    files=LOG_FILES)
                log_manager.create_log_dir()
                log_dir = Path(log_manager.log_dir)  # type: ignore
                if cli_args.reuse_log_dir is None:
                    extra_args.append(f'--reuse-log-dir={log_dir}')
            else:
                playback_log_path = Path(cli_args.playback_log)
                # True if full log path is specified.
                if playback_log_path.exists():
                    if playback_log_path.is_dir():
                        log_dir = playback_log_path / PLAYBACK_DIR
                    else:
                        log_dir = playback_log_path.parent / PLAYBACK_DIR
                    os.makedirs(log_dir, exist_ok=True)
                # Fallback to write the console output to /tmp.
                else:
                    log_dir = Path(gettempdir())

            # Check to see if we need to pass an index CLI arg to the child process.
            if is_multi_test_set:
                env_args_dict['HITL_TEST_SET_INDEX'] = i
                extra_args.append('--test-set-index')
                extra_args.append(str(i))

            # Start HITL as subprocess and monitor it. Write all the output to a console file.
            run_env_args = HitlEnvArgs(**env_args_dict)
            logger.info(f'Starting HITL runner run {i}: {test_type.name}')
            with open(log_dir / CONSOLE_FILE, 'w') as console_out:
                process_timeout_sec = test_type.get_test_params().duration_sec + BUILD_AND_SETUP_TIMEOUT_SEC
                cmd_args[0] = str(RUNNER_SCRIPT_PATH)
                CMD_ARGS = cmd_args + extra_args
                start_time = time.monotonic()
                ret_status = None
                with subprocess.Popen(CMD_ARGS, stdout=console_out, stderr=subprocess.STDOUT, text=True) as proc:
                    with open(log_dir / CONSOLE_FILE, 'r') as console_out_reader:
                        while time.monotonic() - start_time < process_timeout_sec:
                            ret_status = proc.poll()
                            new_text = console_out_reader.read()
                            print(new_text, end='')
                            if ret_status is None:
                                time.sleep(UPDATE_INTERVAL_SEC)
                            else:
                                break

                    if ret_status is None:
                        report_failure(
                            f'HITL runner timed out, killing process. See attached `{CONSOLE_FILE}` in reply for details.',
                            env_args=run_env_args,
                            log_base_dir=cli_args.logs_base_dir,
                            log_dir=log_dir)
                        proc.kill()
                        time.sleep(KILL_TIMEOUT_SEC)
                        sys.exit(1)
                    elif ret_status != 0:
                        # Suppress error report for exit code "10".
                        if ret_status != 10:
                            report_failure(
                                f'HITL process exited with error code {ret_status}. See attached `{CONSOLE_FILE}` in'
                                f' reply for details.',
                                env_args=run_env_args,
                                log_base_dir=cli_args.logs_base_dir,
                                log_dir=log_dir)
                        else:
                            logger.warning('HITL process exited with error code 10. Suppressing failure report.')
                        sys.exit(ret_status)
                    else:
                        logger.info('HITL ran successfully.')
                        # Don't bother trying to track down report for playback if log was specified by GUID.
                        if str(log_dir) != gettempdir():
                            failure_path = log_dir / FAILURE_REPORT
                            report_path = log_dir / FULL_REPORT
                            if not report_path.exists():
                                report_failure(
                                    f'Failed to generate report. See attached `{CONSOLE_FILE}` in reply for details.',
                                    env_args=run_env_args,
                                    log_base_dir=cli_args.logs_base_dir,
                                    log_dir=log_dir)
                                sys.exit(1)
                            else:
                                if failure_path.exists():
                                    with open(failure_path) as fd:
                                        failures = json.load(fd)
                                    failed_tests = ['* ' + f['name'] for f in failures]
                                    failed_tests_str = '\n'.join(failed_tests)
                                    report_failure(
                                        f'Test metric failures detected:\n{failed_tests_str}\n'
                                        f'See attached `{FAILURE_REPORT}` in reply for details.',
                                        env_args=run_env_args,
                                        log_base_dir=cli_args.logs_base_dir,
                                        log_dir=log_dir)
                                    sys.exit(1)
                                else:
                                    logger.info('All tests passed.')
    # The exit calls trigger this exception.
    except SystemExit:
        raise
    except:
        report_failure(
            'Problem running HITL subprocess. This is likely an issue with the HITL SW:\n```' + traceback.format_exc() +
            '```', env_args=env_args, log_base_dir=cli_args.logs_base_dir, log_dir=log_dir)
        raise
    sys.exit(0)


if __name__ == '__main__':
    main()
