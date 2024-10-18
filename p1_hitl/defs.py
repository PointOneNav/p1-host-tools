import json
import logging
import os
from argparse import ArgumentParser, Namespace
from enum import Enum, auto
from pathlib import Path
from typing import List, NamedTuple, Optional

from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR

from p1_runner.device_type import DeviceType

logger = logging.getLogger('point_one.hitl.runner')


PLAYBACK_DIR = 'playback'
FAILURE_REPORT = 'hitl_failures.json'
FULL_REPORT = 'hitl_report.json'
CONSOLE_FILE = 'hitl_console.txt'
UPLOADED_LOG_LIST_FILE = 'uploaded_device_logs.txt'
MSG_TIME_LOG_FILENAME = 'msg_times.bin'
LOG_FILES = [FAILURE_REPORT, FULL_REPORT, CONSOLE_FILE, UPLOADED_LOG_LIST_FILE, MSG_TIME_LOG_FILENAME]


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

    @classmethod
    def from_string(cls, val: str):
        return cls[val.upper()]

    def get_test_params(self) -> TestParams:
        if self == TestType.CONFIGURATION:
            # This test doesn't have a fixed duration. The duration is determined by
            # how long the device takes to respond to commands.
            return TestParams(0, False, False)
        elif self == TestType.SANITY:
            return TestParams(5 * 60, False, False)
        elif self == TestType.ROOF_15_MIN:
            return TestParams(15 * 60, True, True)
        elif self == TestType.ROOF_NO_CORRECTIONS_15_MIN:
            return TestParams(15 * 60, True, False)
        else:
            raise NotImplementedError(f'Metric configuration for {self.name} is not implemented.')


class HitlEnvArgs(NamedTuple):
    # Name for device being tested.
    HITL_NAME: str
    # Path to nautilus repo. Just used to check git info.
    HITL_NAUTILUS_PATH: str
    # The HITL test set to perform.
    HITL_TEST_TYPE: TestType
    # The @ref DeviceType being tested.
    HITL_BUILD_TYPE: DeviceType
    # The version string for the build to run on the device. It can be either:
    # 1. The version string of an existing build to provision the device with (e.x. v2.1.0-920-g6090626b66).
    # 2. The commit-ish of the nautilus repo to get a version string from.
    HITL_DUT_VERSION: str
    # The truth location of the device antenna. It is specified as a the
    # geodetic latitude, longitude, and altitude (in degrees/degrees/meters),
    # expressed using the WGS-84 reference ellipsoid.
    JENKINS_ANTENNA_LOCATION: Optional[tuple[float, float, float]] = None
    # Only for Atlas Tests
    JENKINS_ATLAS_LAN_IP: Optional[str] = None
    JENKINS_ATLAS_BALENA_UUID: Optional[str] = None
    # Slack Credentials
    JENKINS_SLACK_CLIENT_ID: Optional[str] = None
    JENKINS_SLACK_CLIENT_SECRET: Optional[str] = None
    JENKINS_SLACK_VERIFICATION_TOKEN: Optional[str] = None
    JENKINS_SLACK_BOT_TOKEN: Optional[str] = None
    JENKINS_SLACK_CHANNEL: Optional[str] = None

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
    def get_env_args(cls, env_in_dict=os.environ) -> Optional['HitlEnvArgs']:
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
                    else:
                        env_dict[arg] = env_in_dict[arg]
                except (KeyError, ValueError):
                    logger.error(f'Invalid value "{env_in_dict[arg]}" for {arg}')
                    return None
        try:
            return HitlEnvArgs(**env_dict)
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
    def load_env_json_file(cls, in_path: Path) -> Optional['HitlEnvArgs']:
        with open(in_path, 'r') as fd:
            env = json.load(fd)
            return cls.get_env_args(env)


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
    cli_args = parser.parse_args()

    if cli_args.env_file:
        env_args = HitlEnvArgs.load_env_json_file(cli_args.env_file)
    else:
        env_args = HitlEnvArgs.get_env_args()

    return cli_args, env_args
