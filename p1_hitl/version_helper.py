import logging
import re
from typing import Optional

from p1_hitl.defs import DeviceType, HitlEnvArgs, TestType
from p1_hitl.git_cmds import GitWrapper

DEVICE_TAG_GLOB = {
    DeviceType.ATLAS: 'v?.*',
    DeviceType.LG69T_AM: 'lg69t-am-v?.*',
}

logger = logging.getLogger('point_one.hitl.runner')


def git_describe_dut_version(args: HitlEnvArgs) -> Optional[str]:
    git = GitWrapper(args.HITL_NAUTILUS_PATH)
    try:
        return git.describe(DEVICE_TAG_GLOB[args.HITL_BUILD_TYPE], args.HITL_DUT_VERSION)
    except RuntimeError as e:
        logger.warning(f'Unable to git describe commitish {args.HITL_DUT_VERSION}: {e}')
        return None
