'''!
Rules for when to ignore HITL failures.

These are generally real failures that should be handled, but are being silenced since the timeline to address them is
long and they already have tickets.
'''

import logging
from typing import Any

from p1_hitl.defs import HitlEnvArgs, TestType
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

    ignore_failure = False

    # Lots of known LG69T failures.
    if env_args.HITL_BUILD_TYPE.is_lg69t():
        msg_start = 'Slack ignores known LG69T failure: '
        ignored_metrics = [
            'max_velocity',
            'fixed_max_velocity',
            'cpu_usage',
            'seq_num_check',
            'time_between_reset_and_invalid',
            'imu_msg_period',
            'pose_host_time_elapsed',
            'host_time_between_messages',
        ]
        if failure['name'] == 'no_error_msgs':
            if 'Unable to allocate ImuMeasurement' in failure['context']:
                logger.warning(msg_start + '"Unable to allocate ImuMeasurement" event.')
                return True
            elif 'Timed out waiting for Teseo cold start' in failure['context']:
                logger.warning(msg_start + '"Timed out waiting for Teseo cold start" event.')
                return True
        elif failure['name'] == 'monotonic_p1time' and float(failure['context']) < 0.5:
            ignore_failure = True
        elif failure['name'] in ignored_metrics:
            ignore_failure = True
    elif env_args.HITL_BUILD_TYPE is DeviceType.ATLAS:
        if env_args.get_selected_test_type() is TestType.RESET_TESTS:
            ignore_failure = failure['name'] in [
                '2d_fixed_pos_error',
                '3d_fixed_pos_error',
                'time_between_cold_invalid_and_valid',
                'time_between_cold_invalid_and_fixed',
                'imu_msg_period',
                'mem_usage',
            ]
        else:
            ignore_failure = failure['name'] in ['imu_msg_period']
    elif env_args.HITL_BUILD_TYPE is DeviceType.AMAZON_FLEETEDGE_V1:
        ignore_failure = failure['name'] in [
            'fixed_max_velocity',
            'gps_time_valid',
        ]

    if ignore_failure:
        logger.warning('Slack ignores whitelisted failure: ' + failure['name'])

    return ignore_failure
