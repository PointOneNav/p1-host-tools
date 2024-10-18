#!/usr/bin/env python
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import gettempdir

# isort: split

# Add the host tool root directory and device_interfaces to the python path.
repo_root = Path(__file__).parents[1].resolve()
sys.path.append(str(repo_root))

from p1_hitl.defs import CONSOLE_FILE, PLAYBACK_DIR, get_args, FAILURE_REPORT, FULL_REPORT
from p1_runner.log_manager import LogManager

# TODO: Slack integration.

# Give the process 30 minutes above the test duration for build and setup.
BUILD_AND_SETUP_TIMEOUT_SEC = 30 * 60
KILL_TIMEOUT_SEC = 10
UPDATE_INTERVAL_SEC = 1
RUNNER_SCRIPT_PATH = repo_root / 'p1_hitl/hitl_runner.py'


def main():
    try:
        cli_args, env_args = get_args()
    except:
        env_args = None
    if env_args is None:
        print('Problem evaluating arguments for running HITL.')
        sys.exit(1)

    try:
        cmd_args = sys.argv
        extra_args = []

        # There's no real reason to run playback through the wrapper, but don't create a log directory if it occurs.
        if not cli_args.playback_log:
            log_manager = LogManager(
                device_id=env_args.HITL_NAME,
                device_type=env_args.HITL_BUILD_TYPE.name,
                logs_base_dir=cli_args.logs_base_dir,
                directory_to_reuse=cli_args.reuse_log_dir)
            log_manager.create_log_dir()
            log_dir: str = log_manager.log_dir  # type: ignore
            if cli_args.reuse_log_dir is None:
                extra_args.append(f'--reuse-log-dir={log_dir}')
        else:
            playback_log_path = Path(cli_args.playback_log)
            # True if full log path is specified.
            if playback_log_path.exists():
                if playback_log_path.is_dir():
                    log_dir = str(playback_log_path / PLAYBACK_DIR)
                else:
                    log_dir = str(playback_log_path.parent / PLAYBACK_DIR)
                os.makedirs(log_dir, exist_ok=True)
            # Fallback to write the console output to /tmp.
            else:
                log_dir = gettempdir()

        # Start HITL as subprocess and monitor it. Write all the output to a console file.
        with open(Path(log_dir) / CONSOLE_FILE, 'w') as console_out:
            process_timeout_sec = env_args.HITL_TEST_TYPE.get_test_params().duration_sec + BUILD_AND_SETUP_TIMEOUT_SEC
            cmd_args[0] = str(RUNNER_SCRIPT_PATH)
            CMD_ARGS = cmd_args + extra_args
            start_time = time.monotonic()
            ret_status = None
            with subprocess.Popen(CMD_ARGS, stdout=console_out, stderr=subprocess.STDOUT, text=True) as proc:
                with open(Path(log_dir) / CONSOLE_FILE, 'r') as console_out_reader:
                    while time.monotonic() - start_time < process_timeout_sec:
                        ret_status = proc.poll()
                        new_text = console_out_reader.read()
                        print(new_text, end='')
                        if ret_status is None:
                            time.sleep(UPDATE_INTERVAL_SEC)
                        else:
                            break

                if ret_status is None:
                    print('HITL runner timed out, killing process.')
                    proc.kill()
                    time.sleep(KILL_TIMEOUT_SEC)
                    sys.exit(1)
                elif ret_status != 0:
                    print(f'HITL process exited with error code {ret_status}.')
                    sys.exit(ret_status)
                else:
                    print('HITL ran successfully.')
                    # Don't bother trying to track down report for playback if log was specified by GUID.
                    if log_dir != gettempdir():
                        failure_path = Path(log_dir) / FAILURE_REPORT
                        report_path = Path(log_dir) / FULL_REPORT
                        if not report_path.exists():
                            print('Failed to generate report.')
                        else:
                            if failure_path.exists():
                                print('Failures detected.')
                            else:
                                print('All tests passed.')
                                sys.exit(0)
                    sys.exit(1)
    # The exit calls trigger this exception.
    except SystemExit:
        raise
    except:
        print('Problem running HITL subprocess.')
        raise


if __name__ == '__main__':
    main()
