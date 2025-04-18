import math
from typing import Optional

from fusion_engine_client.messages import (GNSSAttitudeOutput, SolutionType,
                                           Timestamp)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CdfThreshold,
                                             MetricController, StatsMetric)

from .base_analysis import AnalyzerBase

metric_gnss_attitude_valid_p1_time = AlwaysTrueMetric(
    'gnss_attitude_valid_p1_time',
    "All attitude messages should have a valid P1 time.",
)

metric_gnss_attitude_period = StatsMetric(
    'gnss_attitude_period',
    'The time between attitude messages should be close to the attitude\'s expected rate. If this check is skipped, no '
    'attitude messages were received.',
    # The actual thresholds are set based on the device in `configure_metrics()`.
    is_logged=True,
    is_required=True,
)

metric_gnss_attitude_fixed = AlwaysTrueMetric(
    'gnss_attitude_fixed',
    'All attitudes should be RTK fixed after initialization. (i.e., the attitude should not lose fix or reset). If '
    'this check is skipped, either no attitude messages were received or none were RTK fixed.',
    is_required=True,
)

metric_gnss_attitude_non_nan = AlwaysTrueMetric(
    'gnss_attitude_non_nan',
    'ypr_deg and baseline_distance_m should be non-nan if the output is marked valid.',
)

metric_gnss_attitude_yaw_error = StatsMetric(
    'gnss_attitude_yaw_error',
    'attitude message yaw error (deg) stats when RTK fixed.',
    max_threshold=2,
    max_cdf_thresholds=[
        CdfThreshold(68, 0.4),
    ],
    is_logged=True,
)

metric_gnss_attitude_baseline_error = StatsMetric(
    'gnss_attitude_baseline_error',
    'attitude message baseline error (m) stats when RTK fixed.',
    max_threshold=1.0,
    max_cdf_thresholds=[
        CdfThreshold(90, .1),
        CdfThreshold(50, .08),
    ],
    is_logged=True,
)


def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.get_selected_test_type().get_test_params()
    heading_metrics = MetricController.get_metrics_in_this_file()
    if not env_args.HITL_BUILD_TYPE.has_attitude():
        for metric in heading_metrics:
            metric.is_disabled = True

    else:
        nominal_period_sec = 0.1
        max_tolerance_sec = 0.01
        percentile_50_tolerance_sec = 0.001
        metric_gnss_attitude_period.max_threshold = nominal_period_sec + max_tolerance_sec
        metric_gnss_attitude_period.min_threshold = nominal_period_sec - max_tolerance_sec
        metric_gnss_attitude_period.max_cdf_thresholds = [
            CdfThreshold(50, nominal_period_sec + percentile_50_tolerance_sec)]
        metric_gnss_attitude_period.min_cdf_thresholds = [
            CdfThreshold(50, nominal_period_sec - percentile_50_tolerance_sec)]

        # Don't require fixing if positioning isn't expected.
        if not params.check_position:
            metric_gnss_attitude_fixed.is_disabled = True

        # Disable some checks if the test is sending reset commands.
        if params.has_resets:
            metric_gnss_attitude_fixed.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


class HeadingAnalyzer(AnalyzerBase):
    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        if env_args.JENKINS_DUEL_ANTENNA_ATTITUDE is None:
            if self.env_args.HITL_BUILD_TYPE.has_attitude() and self.params.check_position:
                raise KeyError('JENKINS_DUEL_ANTENNA_ATTITUDE must be specified to test heading metrics.')
            self.true_yaw_deg = None
            self.true_baseline_m = None
        else:
            self.true_yaw_deg = env_args.JENKINS_DUEL_ANTENNA_ATTITUDE[0]
            self.true_baseline_m = env_args.JENKINS_DUEL_ANTENNA_ATTITUDE[1]

        self.last_p1_time: Optional[Timestamp] = None
        self.is_valid = False

    def update(self, msg: MessageWithBytesTuple):
        if not self.env_args.HITL_BUILD_TYPE.has_attitude():
            return

        _, payload, _ = msg
        if isinstance(payload, GNSSAttitudeOutput):
            metric_gnss_attitude_valid_p1_time.check(bool(payload.get_p1_time()))
            # Skip further processing if no P1 time is present.
            if not payload.get_p1_time():
                return

            if self.last_p1_time is not None:
                time_diff = payload.get_p1_time().seconds - self.last_p1_time.seconds
                metric_gnss_attitude_period.check(time_diff)
            self.last_p1_time = payload.get_p1_time()

            if payload.solution_type != SolutionType.Invalid:
                failure_context = ''
                # Note that pitch and roll are allowed to be NaN.
                if math.isnan(payload.ypr_deg[0]):
                    failure_context = 'yaw had a NaN value.'
                elif math.isnan(payload.baseline_distance_m):
                    failure_context = 'baseline distance had a NaN value.'
                metric_gnss_attitude_non_nan.check(len(failure_context) == 0, failure_context)

            if payload.solution_type != SolutionType.RTKFixed:
                if self.is_valid:
                    metric_gnss_attitude_fixed.check(False)
                return
            self.is_valid = True
            metric_gnss_attitude_fixed.check(True)

            if self.true_yaw_deg is not None:
                # Normalize angles to be between 0 and 360 degrees
                yaw1_deg = payload.ypr_deg[0] % 360.0
                yaw2_deg = self.true_yaw_deg % 360.0
                yaw_error_deg = abs(yaw1_deg - yaw2_deg)
                # Choose the smaller error clockwise vs. counter-clockwise.
                yaw_error_deg = min(yaw_error_deg, 360.0 - yaw_error_deg)
                metric_gnss_attitude_yaw_error.check(yaw_error_deg)

            if self.true_baseline_m is not None:
                baseline_error_m = abs(payload.baseline_distance_m - self.true_baseline_m)
                metric_gnss_attitude_baseline_error.check(baseline_error_m)
