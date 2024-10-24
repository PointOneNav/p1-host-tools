import numpy as np
from fusion_engine_client.analysis.attitude import get_enu_rotation_matrix
from fusion_engine_client.messages import PoseMessage, SolutionType
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple
from pymap3d import geodetic2ecef

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CdfThreshold,
                                             MaxValueMetric, MetricController,
                                             PercentTrueMetric, StatsMetric)

from .base_analysis import AnalyzerBase

metric_fix_rate = PercentTrueMetric(
    'fix_rate',
    'Percent of solutions in fix mode.',
    90.0,
    is_required=True,
    not_logged=True
)

metric_position_valid = AlwaysTrueMetric(
    'position_valid',
    'All positions should be valid.',
    is_required=True,
    not_logged=True
)

metric_max_velocity = MaxValueMetric(
    'max_velocity',
    'Velocity (mps) should be near 0.',
    0.01,
    is_required=True,
)

metric_2d_fixed_pos_error = StatsMetric(
    '2d_fixed_pos_error',
    '2d fixed position error (m) stats.',
    max_threshold=0.5,
    max_cdf_thresholds=[
        CdfThreshold(90, .1),
        CdfThreshold(50, .08),
    ],
    is_required=True
)

metric_3d_fixed_pos_error = StatsMetric(
    '3d_fixed_pos_error',
    '3d fixed position error (m) stats.',
    max_threshold=0.5,
    max_cdf_thresholds=[
        CdfThreshold(90, .1),
        CdfThreshold(50, .08),
    ],
    is_required=True
)

metric_no_nan_in_position = AlwaysTrueMetric(
    'non_nan_position',
    'All positions should be non-nan values.',
    is_required=True,
    not_logged=True
)

def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.HITL_TEST_TYPE.get_test_params()
    position_metrics = MetricController.get_metrics_in_this_file()
    if not params.check_position:
        for metric in position_metrics:
            metric.is_disabled = True
    elif not params.has_corrections:
        metric_fix_rate.is_disabled = True
        metric_2d_fixed_pos_error.is_disabled = True
        metric_3d_fixed_pos_error.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


def calculate_position_error(device_lla_deg, reference_lla_deg) -> tuple[float, float]:
    device_ecef = np.array(geodetic2ecef(
        *device_lla_deg, deg=True))

    reference_ecef = np.array(geodetic2ecef(
        *reference_lla_deg, deg=True))

    error_ecef_m = device_ecef - reference_ecef
    c_enu_ecef = get_enu_rotation_matrix(*device_lla_deg[0:2], deg=True)
    error_enu_m = c_enu_ecef.dot(error_ecef_m)

    return np.linalg.norm(error_enu_m[:2], axis=0), np.linalg.norm(error_enu_m, axis=0)


class PositionAnalyzer(AnalyzerBase):
    def configure(self, env_args: HitlEnvArgs):
        self.env_args = env_args
        self.params = env_args.HITL_TEST_TYPE.get_test_params()
        if self.params.check_position and env_args.JENKINS_ANTENNA_LOCATION is None:
            raise KeyError(
                f'JENKINS_ANTENNA_LOCATION must be specified test {env_args.HITL_TEST_TYPE.name} with position checking.')

    def update(self, msg: MessageWithBytesTuple):
        if self.params.check_position is False:
            return

        _, payload, _ = msg
        if isinstance(payload, PoseMessage):
            is_fixed = payload.solution_type == SolutionType.RTKFixed
            is_valid = payload.solution_type != SolutionType.Invalid
            metric_fix_rate.check(is_fixed)
            metric_position_valid.check(is_valid)

            position_is_non_nan = not np.any(np.isnan(payload.lla_deg))
            metric_no_nan_in_position(position_is_non_nan)

            if is_valid:
                velocity_mps = float(np.linalg.norm(payload.velocity_body_mps))
                metric_max_velocity.check(velocity_mps)

                error_2d_m, error_3d_m = calculate_position_error(
                    payload.lla_deg, self.env_args.JENKINS_ANTENNA_LOCATION)

                if is_fixed:
                    metric_2d_fixed_pos_error.check(error_2d_m)
                    metric_3d_fixed_pos_error.check(error_3d_m)
