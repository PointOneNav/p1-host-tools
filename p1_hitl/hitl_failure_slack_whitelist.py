'''!
Rules for when to ignore HITL failures.

These are generally real failures that should be handled, but are being silenced since the timeline to address them is
long and they already have tickets.
'''

import logging
from typing import Any

from p1_hitl.defs import HitlEnvArgs
from p1_runner.device_type import DeviceType

logger = logging.getLogger('point_one.hitl.failure_whitelist')


def should_configuration_be_ignored(env_args: HitlEnvArgs) -> bool:
    # As of now, no device failures are whitelisted.
    return False


def should_failure_be_ignored(env_args: HitlEnvArgs, failure: dict[str, Any]) -> bool:
    # failure:
    # {
    #     'name': name,
    #     'type': type(metric).__name__,
    #     'description': metric.description,
    #     'context': metric.failure_context
    # }

    # Lots of known LG69T failures.
    if env_args.HITL_BUILD_TYPE.is_lg69t():
        msg_start = 'Slack ignores known LG69T failure: '
        ignored_metrics = ['max_velocity', 'fixed_max_velocity', 'cpu_usage', 'seq_num_check']
        if failure['name'] == 'no_error_msgs' and 'Unable to allocate ImuMeasurement' in failure['context']:
            logger.warning(msg_start + '"Unable to allocate ImuMeasurement" event.')
            return True
        elif failure['name'] == 'monotonic_p1time' and float(failure['context']) < 0.5:
            logger.warning(msg_start + 'monotonic_p1time')
            return True
        elif failure['name'] in ignored_metrics:
            logger.warning(msg_start + failure['name'])
            return True

    return False
