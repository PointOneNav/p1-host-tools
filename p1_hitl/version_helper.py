import logging
import re
from typing import Optional

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.git_cmds import GitWrapper
from p1_runner.device_type import DeviceType

logger = logging.getLogger('point_one.hitl.runner')


def git_describe_dut_version(args: HitlEnvArgs) -> Optional[str]:
    git = GitWrapper(args.HITL_NAUTILUS_PATH)
    try:
        mapping = DeviceType.mapping_device_to_regex()
        regex = mapping.get(args.HITL_BUILD_TYPE, None)
        if regex is None:
            raise Exception(f'Build type {args.HITL_BUILD_TYPE} not found.')

        return git.describe(regex, args.HITL_DUT_VERSION)

    except RuntimeError as e:
        logger.warning(f'Unable to git describe commitish {args.HITL_DUT_VERSION}: {e}')
        return None
    except Exception as e:
        logger.warning(f'Failed to map build type to supported device types: {e}')
        return None
