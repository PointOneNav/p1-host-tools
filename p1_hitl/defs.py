import logging
import os
import re
from enum import Enum, auto
from typing import List, NamedTuple, Optional

logger = logging.getLogger('point_one.hitl.runner')


class BuildType(Enum):
    ATLAS = auto()
    LG69T_AM = auto()

    def is_lg69t(self):
        return self in [BuildType.LG69T_AM]

    @classmethod
    def from_str(cls, val: str):
        return cls[val.upper()]

    @classmethod
    def get_build_type_from_version(cls, version_str) -> Optional['BuildType']:
        # Determine path to the auto-generated config loading code on S3.
        if re.match(r'lg69t-am-', version_str):
            return BuildType.LG69T_AM
        elif re.match(r'v\d+\.\d+\.\d+', version_str):
            return BuildType.ATLAS
        else:
            logger.error(f'Unable to infer BuildType from version_str ({version_str}).')
            return None


class TestType(Enum):
    CONFIGURATION = auto()

    @classmethod
    def from_str(cls, val: str):
        return cls[val.upper()]


class HiltEnvArgs(NamedTuple):
    # Name for device being tested.
    HITL_NAME: str
    # Path to nautilus repo. Just used to check git info.
    HITL_NAUTILUS_PATH: str
    # The HITL test set to perform.
    HITL_TEST_TYPE: TestType
    # The @ref BuildType being tested.
    HITL_BUILD_TYPE: BuildType
    # A nautilus "commit-ish" to checkout and build if needed.
    HITL_BUILD_COMMIT: Optional[str] = None
    # The version string for the build to run on the device.
    HITL_DUT_VERSION: Optional[str] = None
    # Only for Atlas Tests
    JENKINS_ATLAS_LAN_IP: Optional[str] = None
    JENKINS_ATLAS_BALENA_UUID: Optional[str] = None

    def check_fields(self, required_fields: List[str]) -> bool:
        ret = True
        for field in required_fields:
            if not hasattr(self, field):
                raise KeyError(f'HiltEnvArgs does not contain field "{field}".')
            elif getattr(self, field) is None:
                logger.warning(f'Environment arguments missing required field "{field}".')
                ret = False
        return ret

    @classmethod
    def get_env_args(cls) -> Optional['HiltEnvArgs']:
        env_dict = {}
        for arg in HiltEnvArgs._fields:
            if arg in os.environ:
                try:
                    if arg == 'HITL_TEST_TYPE':
                        env_dict[arg] = TestType.from_str(os.environ[arg])
                    elif arg == 'HITL_BUILD_TYPE':
                        env_dict[arg] = BuildType.from_str(os.environ[arg])
                    else:
                        env_dict[arg] = os.environ[arg]
                except KeyError:
                    logger.error(f'Invalid value "{os.environ[arg]}" for {arg}')
                    return None
        try:
            return HiltEnvArgs(**env_dict)
        except Exception as e:
            logger.error(f'Failure loading expected environment variables: {e}')
            return None
