import numpy as np
from fusion_engine_client.analysis.attitude import get_enu_rotation_matrix
from fusion_engine_client.messages import PoseMessage, SolutionType
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple
from pymap3d import geodetic2ecef

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CdfThreshold,
                                             MaxArrayValueMetric,
                                             MaxTimeBetweenChecks,
                                             MaxTimeToFirstCheckMetric,
                                             MaxValueMetric, MetricController,
                                             PercentTrueMetric, StatsMetric,
                                             TimeSource)

from .base_analysis import AnalyzerBase

metric_fix_rate = PercentTrueMetric(
    'fix_rate',
    'Percent of solutions in fix mode.',
    90.0,
    is_required=True,
)

metric_position_valid = AlwaysTrueMetric(
    'position_valid',
    'All positions should be valid.',
    is_required=True,
)

metric_p1_time_valid = AlwaysTrueMetric(
    'p1_time_valid',
    'All P1 times should be valid.',
    is_required=True,
)

metric_host_time_to_first_solution = MaxTimeToFirstCheckMetric(
    'host_time_to_first_solution',
    'Time to get any valid solution.',
    time_source=TimeSource.HOST,
    max_time_to_first_check_sec=10.0
)

metric_pose_host_time_elapsed = MaxTimeBetweenChecks(
    'pose_host_time_elapsed',
    'Max host time to first pose message, and between subsequent messages.',
    TimeSource.HOST,
    # Ideally, this should be specified for each device. I'm going to set this
    # conservatively initially, and bring down once we have better testing
    # integration and can make sure it doesn't generate false positives.
    max_time_between_checks_sec=0.5,
)

metric_pose_p1_time_elapsed = MaxTimeBetweenChecks(
    'pose_time_elapsed',
    'Max P1 time between pose messages.',
    TimeSource.P1,
    # Ideally, this should be specified for each device. I'm going to set this
    # conservatively initially, and bring down once we have better testing
    # integration and can make sure it doesn't generate false positives.
    max_time_between_checks_sec=0.3,
)

metric_gps_time_valid = AlwaysTrueMetric(
    'gps_time_valid',
    'All GPS times should be valid.',
    is_required=True,
)

metric_fixed_max_velocity = MaxValueMetric(
    'fixed_max_velocity',
    'Velocity (mps) when fixed should be near 0.',
    0.01,
    is_required=True,
    is_logged=True,
)

metric_max_velocity = MaxValueMetric(
    'max_velocity',
    'Velocity (mps) should be near 0.',
    0.1,
    is_required=True,
    is_logged=True,
)

metric_2d_fixed_pos_error = StatsMetric(
    '2d_fixed_pos_error',
    '2d fixed position error (m) stats.',
    max_threshold=0.5,
    max_cdf_thresholds=[
        CdfThreshold(90, .1),
        CdfThreshold(50, .08),
    ],
    is_logged=True,
)

metric_3d_fixed_pos_error = StatsMetric(
    '3d_fixed_pos_error',
    '3d fixed position error (m) stats.',
    max_threshold=0.5,
    max_cdf_thresholds=[
        CdfThreshold(90, .1),
        CdfThreshold(50, .08),
    ],
    is_logged=True,
)

metric_2d_pos_error = StatsMetric(
    '2d_pos_error',
    '2d position error (m) stats.',
    max_threshold=20.0,
    max_cdf_thresholds=[
        CdfThreshold(90, 10.0),
        CdfThreshold(50, 2.0),
    ],
    is_logged=True,
)

metric_3d_pos_error = StatsMetric(
    '3d_pos_error',
    '3d position error (m) stats.',
    max_threshold=20.0,
    max_cdf_thresholds=[
        CdfThreshold(90, 10.0),
        CdfThreshold(50, 2.0),
    ],
    is_logged=True,
)

metric_non_nan_position = AlwaysTrueMetric(
    'non_nan_position',
    'All positions should be non-nan values.',
    is_required=True,
)

metric_delta_ypr_deg = MaxArrayValueMetric(
    'delta_ypr_deg',
    'Max jumps in YPR values should be lower than [5.0, 5.0, 5.0]',
    [5.0, 5.0, 5.0],
    is_required=True,
)

metric_fixed_pos_std_enu = MaxArrayValueMetric(
    'fixed_pos_std_enu',
    'ENU position standard deviations when fixed should be less than [2.0, 2.0, 2.0]',
    [2.0, 2.0, 2.0],
)

metric_ypr_std_deg = MaxArrayValueMetric(
    'ypr_std_deg',
    'Max YPR standard deviations should be lower than [5.0, 5.0, 5.0]',
    [5.0, 5.0, 5.0],
    is_required=True,
)

metric_vel_std_mps = MaxArrayValueMetric(
    'vel_std_mps',
    'Max velocity standard deviations should be lower than [3.0, 3.0, 3.0]',
    [3.0, 3.0, 3.0],
    is_required=True,
)

metric_non_nan_pos_std_enu = AlwaysTrueMetric(
    'non_nan_pos_std_enu',
    'ENU position standard deviations should be non-nan values.',
    is_required=True,
)

metric_non_nan_ypr_std_deg = AlwaysTrueMetric(
    'non_nan_ypr_std_deg',
    'YPR standard deviations should be non-nan values.',
    is_required=True,
)

metric_non_nan_vel_std_mps = AlwaysTrueMetric(
    'non_nan_vel_std_mps',
    'Velocity standard deviations should be non-nan values.',
    is_required=True,
)

metric_non_nan_undulation = AlwaysTrueMetric(
    'non_nan_undulation',
    'Undulation should be non-nan value.',
    is_required=True,
)


def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.get_selected_test_type().get_test_params()
    position_metrics = MetricController.get_metrics_in_this_file()
    if not params.check_position:
        for metric in position_metrics:
            metric.is_disabled = True
    else:
        if not params.has_corrections:
            metric_fix_rate.is_disabled = True
            metric_fixed_max_velocity.is_disabled = True
            metric_2d_fixed_pos_error.is_disabled = True
            metric_3d_fixed_pos_error.is_disabled = True

        # YPR will be nan before the filter initializes orientation. For HITL where the receiver is not moving, I would
        # expect that to be 100% of the time. If TightEsrif manages to initialize orientation without moving, that should
        # actually be considered a bug.
        #
        # Position and velocity should initialize when the filter does (however we need to be careful about fallback
        # positions before filter initialization for devices where we are doing that)
        #
        # The one counter for (1) would be a test where we're injecting a hot start state to begin with. In that case,
        # assuming it has orientation initialized (which is not required for all hot starts), it should continue outputting
        # basically the same angles forever since it's not moving. That's a test we might want to check
        if params.is_stationary:
            metric_delta_ypr_deg.is_disabled = True
            metric_ypr_std_deg.is_disabled = True
            metric_non_nan_ypr_std_deg.is_disabled = True
            metric_non_nan_vel_std_mps.is_disabled = True

        if env_args.HITL_BUILD_TYPE.is_lg69t():
            # To speed up TTFF initially allow positions without GPSTime.
            metric_gps_time_valid.is_disabled = True
            metric_max_velocity.threshold = 0.3
            metric_fixed_max_velocity.threshold = 0.1
            # The initial position error can be very large (observed 80m) since it leverages the native solution as a
            # fallback.
            # TODO: This will be better addressed once https://pointonenav.atlassian.net/browse/FUS-3399 is added.
            metric_3d_pos_error.max_threshold = 100
            metric_2d_pos_error.max_threshold = 100

        if not env_args.HITL_BUILD_TYPE.is_gnss_only():
            # Can't resolve ENU position before yaw is initialized which increases position uncertainty.
            metric_fixed_pos_std_enu.is_disabled = True

        # Disable some checks if the test is sending reset commands.
        if params.has_resets:
            metric_fix_rate.is_disabled = True
            metric_position_valid.is_disabled = True
            metric_gps_time_valid.is_disabled = True


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
    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        if self.params.check_position and env_args.JENKINS_ANTENNA_LOCATION is None:
            raise KeyError(
                f'JENKINS_ANTENNA_LOCATION must be specified test {env_args.get_selected_test_type().name} with position checking.')

        self.got_first_valid = False
        self.last_p1_time = None
        self.last_ypr = None

    def update(self, msg: MessageWithBytesTuple):
        if self.params.check_position is False:
            return

        _, payload, _ = msg
        if isinstance(payload, PoseMessage):
            metric_p1_time_valid.check(bool(payload.p1_time))
            metric_pose_p1_time_elapsed.check()
            metric_pose_host_time_elapsed.check()
            is_fixed = payload.solution_type == SolutionType.RTKFixed
            is_valid = payload.solution_type != SolutionType.Invalid

            # Don't factor invalid solutions at startup into other checks.
            if not self.got_first_valid and not is_valid:
                return
            self.got_first_valid = True

            metric_host_time_to_first_solution.check()
            metric_fix_rate.check(is_fixed)
            metric_position_valid.check(is_valid)

            if is_valid:
                position_is_non_nan = not np.any(np.isnan(payload.lla_deg))
                metric_non_nan_position.check(position_is_non_nan)

                metric_gps_time_valid.check(bool(payload.gps_time))

                velocity_mps = float(np.linalg.norm(payload.velocity_body_mps))
                metric_max_velocity.check(velocity_mps)

                error_2d_m, error_3d_m = calculate_position_error(
                    payload.lla_deg, self.env_args.JENKINS_ANTENNA_LOCATION)

                metric_2d_pos_error.check(error_2d_m)
                metric_3d_pos_error.check(error_3d_m)

                if is_fixed:
                    metric_2d_fixed_pos_error.check(error_2d_m)
                    metric_3d_fixed_pos_error.check(error_3d_m)
                    metric_fixed_pos_std_enu.check(payload.position_std_enu_m)
                    metric_fixed_max_velocity.check(velocity_mps)

                if self.last_ypr is not None:
                    metric_delta_ypr_deg.check(np.abs(np.subtract(payload.ypr_deg, self.last_ypr)))
                self.last_ypr = payload.ypr_deg

                metric_non_nan_pos_std_enu.check(not any(np.isnan(payload.position_std_enu_m)))
                metric_non_nan_ypr_std_deg.check(not any(np.isnan(payload.ypr_std_deg)))
                metric_non_nan_vel_std_mps.check(not any(np.isnan(payload.velocity_std_body_mps)))

                metric_ypr_std_deg.check(payload.ypr_std_deg)
                metric_vel_std_mps.check(payload.velocity_std_body_mps)

                metric_non_nan_undulation.check(not np.isnan(payload.undulation_m))
