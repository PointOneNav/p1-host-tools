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
        mapping = DeviceType.mapping_device_to_regex(args.HITL_BUILD_TYPE)
        return git.describe(mapping.get(args.HITL_BUILD_TYPE, 'UNKNOWN'), args.HITL_DUT_VERSION)
    except RuntimeError as e:
        logger.warning(f'Unable to git describe commitish {args.HITL_DUT_VERSION}: {e}')
        return None
