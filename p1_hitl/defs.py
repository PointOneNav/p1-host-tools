import json
import logging
import os
from argparse import ArgumentParser, Namespace
from enum import Enum, auto
from pathlib import Path
from typing import List, NamedTuple, Optional

from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, find_log_file

from p1_runner.device_type import DeviceType

logger = logging.getLogger('point_one.hitl.runner')


PLAYBACK_DIR = 'playback'
FAILURE_REPORT = 'hitl_failures.json'
FULL_REPORT = 'hitl_report.json'
CONSOLE_FILE = 'hitl_console.txt'
UPLOADED_LOG_LIST_FILE = 'uploaded_device_logs.txt'
MSG_TIME_LOG_FILENAME = 'msg_times.bin'
ENV_DUMP_FILE = 'env.json'
BUILD_INFO_FILE = 'build-info.json'
EVENT_NOTIFICATION_FILE = 'event_notifications.txt'
TEST_EVENT_FILE = 'test_events.json'
LOG_FILES = [
    FAILURE_REPORT,
    FULL_REPORT,
    CONSOLE_FILE,
    UPLOADED_LOG_LIST_FILE,
    MSG_TIME_LOG_FILENAME,
    ENV_DUMP_FILE,
    BUILD_INFO_FILE,
    EVENT_NOTIFICATION_FILE,
    TEST_EVENT_FILE,
]


class TestParams(NamedTuple):
    '''!
    @brief The parameters of the test that might effect which analysis to run, or what metric thresholds to use.
    '''
    # How long should the test run for.
    duration_sec: float
    # Should the position output be analyzed.
    check_position: bool
    # Will the device be expected to generate corrected solutions.
    has_corrections: bool
    # Will the device be stationary.
    is_stationary: bool
    # Is the device expected to reset during the scenario.
    has_resets: bool


class TestType(Enum):
    # Special test that exercises configuration functionality.
    # - Does not use normal device runner since the test needs to interact with the device.
    # - May modify device settings
    CONFIGURATION = auto()
    # Check if device starts up and generates expected messages.
    SANITY = auto()
    # Check for positioning performance with stationary clear sky.
    ROOF_15_MIN = auto()
    # Check for positioning performance with stationary clear sky with corrections disabled.
    ROOF_NO_CORRECTIONS_15_MIN = auto()
    # Run test while sending hot, warm, and cold start commands.
    RESET_TESTS = auto()

    # Multi-test scenarios
    NO_TESTS = auto()
    QUICK_TESTS = auto()

    @classmethod
    def from_string(cls, val: str):
        return cls[val.upper()]

    def get_test_set(self) -> List['TestType']:
        if self == TestType.NO_TESTS:
            return []
        if self == TestType.QUICK_TESTS:
            return [TestType.CONFIGURATION, TestType.SANITY]
        else:
            return [self]

    def get_test_params(self) -> TestParams:
        if len(self.get_test_set()) > 1:
            raise ValueError(f"TestType {self.name} is a multi test set. It can't be run directly, and must be"
                             "interpreted by hitl_wrapper.py into its individual tests.")

        if self == TestType.CONFIGURATION:
            # This test doesn't have a fixed duration. The duration is determined by
            # how long the device takes to respond to commands.
            return TestParams(duration_sec=0, check_position=False,
                              has_corrections=False, is_stationary=True, has_resets=False)
        elif self == TestType.SANITY:
            return TestParams(duration_sec=5 * 60, check_position=False,
                              has_corrections=False, is_stationary=True, has_resets=False)
        elif self == TestType.RESET_TESTS:
            return TestParams(duration_sec=8 * 60, check_position=True,
                              has_corrections=True, is_stationary=True, has_resets=True)
        elif self == TestType.ROOF_15_MIN:
            return TestParams(duration_sec=15 * 60, check_position=True,
                              has_corrections=True, is_stationary=True, has_resets=False)
        elif self == TestType.ROOF_NO_CORRECTIONS_15_MIN:
            return TestParams(duration_sec=15 * 60, check_position=True,
                              has_corrections=False, is_stationary=True, has_resets=False)
        else:
            raise NotImplementedError(f'Metric configuration for {self.name} is not implemented.')


class HitlEnvArgs(NamedTuple):
    # Name for device being tested.
    HITL_NAME: str
    # Path to nautilus repo. Just used to check git info.
    HITL_NAUTILUS_PATH: str
    # The HITL test set to perform. NOTE: To get the individual test that is currently running in the case of a
    # multi-scenario set, use `get_selected_test_type()`.
    HITL_TEST_TYPE: TestType
    # The @ref DeviceType being tested.
    HITL_BUILD_TYPE: DeviceType
    # The version string for the build to run on the device. It can be either:
    # 1. The version string of an existing build to provision the device with (e.x. v2.1.0-920-g6090626b66).
    # 2. The commit-ish of the nautilus repo to get a version string from.
    HITL_DUT_VERSION: str
    # An additional string to display with the @ref HITL_DUT_VERSION (e.x. the git branch associated with a commit).
    HITL_VERSION_ANNOTATION: str = ''
    # For a multi-test set, which test in the set to perform.
    HITL_TEST_SET_INDEX: Optional[int] = None
    # The truth location of the device antenna. It is specified as a the
    # geodetic latitude, longitude, and altitude (in degrees/degrees/meters),
    # expressed using the WGS-84 reference ellipsoid.
    JENKINS_ANTENNA_LOCATION: Optional[tuple[float, float, float]] = None
    # Only for test on devices with TCP interfaces.
    JENKINS_LAN_IP: Optional[str] = None
    JENKINS_ATLAS_BALENA_UUID: Optional[str] = None
    # Only for test on devices with UART interfaces.
    JENKINS_UART1: Optional[str] = None
    JENKINS_UART2: Optional[str] = None
    # For devices with an external relay to trigger resets.
    # It is specified as a the RELAY_ID:RELAY_NUMBER (Ex. 6QMBS:1).
    JENKINS_RESET_RELAY: Optional[tuple[str, int]] = None

    def get_selected_test_type(self) -> TestType:
        if self.HITL_TEST_SET_INDEX is None:
            return self.HITL_TEST_TYPE
        else:
            return self.HITL_TEST_TYPE.get_test_set()[self.HITL_TEST_SET_INDEX]

    def check_fields(self, required_fields: List[str]) -> bool:
        ret = True
        for field in required_fields:
            if not hasattr(self, field):
                raise KeyError(f'HitlEnvArgs does not contain field "{field}".')
            elif getattr(self, field) is None:
                logger.warning(f'Environment arguments missing required field "{field}".')
                ret = False
        return ret

    @classmethod
    def get_env_args(cls, env_in_dict=os.environ, test_set_index: Optional[int] = None) -> Optional['HitlEnvArgs']:
        env_dict = {}
        for arg in HitlEnvArgs._fields:
            if arg in env_in_dict:
                try:
                    if arg == 'HITL_TEST_TYPE':
                        env_dict[arg] = TestType.from_string(env_in_dict[arg])
                    elif arg == 'HITL_BUILD_TYPE':
                        env_dict[arg] = DeviceType.from_string(env_in_dict[arg])
                    elif arg == 'JENKINS_ANTENNA_LOCATION':
                        parts = env_in_dict[arg].split(',')
                        if len(parts) == 3:
                            env_dict[arg] = tuple(float(v) for v in parts)
                        else:
                            raise ValueError()
                    elif arg == 'JENKINS_RESET_RELAY':
                        parts = env_in_dict[arg].split(':')
                        if len(parts) == 2:
                            env_dict[arg] = tuple([parts[0], int(parts[1])])
                        else:
                            raise ValueError()
                    else:
                        env_dict[arg] = env_in_dict[arg]
                except (KeyError, ValueError):
                    logger.error(f'Invalid value "{env_in_dict[arg]}" for {arg}')
                    return None
        try:
            if test_set_index is not None:
                env_dict['HITL_TEST_SET_INDEX'] = test_set_index
            env_args = HitlEnvArgs(**env_dict)
            try:
                env_args.get_selected_test_type()
            except IndexError:
                logger.error(f'HITL_TEST_SET_INDEX of {env_args.HITL_TEST_SET_INDEX} is out of range for test set'
                             f'{env_args.HITL_TEST_TYPE.name} length {len(env_args.HITL_TEST_TYPE.get_test_set())}.')
                return None
            return env_args
        except Exception as e:
            logger.error(f'Failure loading expected environment variables: {e}')
            return None

    @classmethod
    def dump_env_to_json_file(cls, out_path: Path):
        env_dict = {}
        for arg in HitlEnvArgs._fields:
            if arg in os.environ:
                env_dict[arg] = os.environ[arg]
        with open(out_path, 'w') as fd:
            json.dump(env_dict, fd)

    @classmethod
    def load_env_json_file(cls, in_path: Path, test_set_index: Optional[int] = None) -> Optional['HitlEnvArgs']:
        with open(in_path, 'r') as fd:
            env = json.load(fd)
            return cls.get_env_args(env, test_set_index=test_set_index)


def get_args() -> tuple[Namespace, Optional[HitlEnvArgs]]:
    parser = ArgumentParser()
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase verbosity.",
    )
    parser.add_argument(
        '--list-metric-only', action='store_true',
        help="Don't perform HITL test. Instead just generate a report with the expected HITL metrics.")
    parser.add_argument(
        '--skip-reset', action='store_true',
        help="Don't reset DUT (used for faster development testing).")
    parser.add_argument(
        '--log-metric-values', action='store_true',
        help="Generate CSV's for each metric in the output directory.")
    parser.add_argument(
        '--logs-base-dir', default=DEFAULT_LOG_BASE_DIR,
        help="The base directory containing FusionEngine logs to be searched and written to.")
    parser.add_argument(
        '--reuse-log-dir', default=None,
        help="Use this directory instead of generating a new log directory.")
    parser.add_argument(
        '-p', '--playback-log',
        help="Rather than connect to a device, re-analyze a log instead.")
    parser.add_argument(
        '-e', '--env-file', type=Path,
        help="Rather than load args from environment, use a JSON file.")
    parser.add_argument(
        '-i', '--test-set-index', type=int,
        help="Override the HITL_TEST_SET_INDEX environment value to set which test in a set to perform.")

    cli_args = parser.parse_args()

    env_file = cli_args.env_file
    # During playback, if required environment value is not found, and not specified over CLI, check playback directory.
    if cli_args.playback_log and env_file is None and os.getenv('HITL_NAME') is None:
        try:
            _, log_dir = find_log_file(
                cli_args.playback_log,
                return_output_dir=True,
                log_base_dir=cli_args.logs_base_dir,
                candidate_files=[
                    'input.raw',
                    'input.p1bin'])
            tmp_env = Path(log_dir) / ENV_DUMP_FILE
            if tmp_env.exists():
                logger.info('Env data not specified in CLI or environment, so loading from playback directory.')
                env_file = tmp_env
        except FileNotFoundError as e:
            pass

    if env_file:
        env_args = HitlEnvArgs.load_env_json_file(env_file, test_set_index=cli_args.test_set_index)
    else:
        env_args = HitlEnvArgs.get_env_args(test_set_index=cli_args.test_set_index)

    return cli_args, env_args
