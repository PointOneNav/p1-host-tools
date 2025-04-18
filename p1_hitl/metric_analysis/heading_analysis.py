import math
from typing import Optional

from fusion_engine_client.messages import (GNSSAttitudeOutput, SolutionType,
                                           Timestamp)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CdfThreshold,
                                             MetricController, StatsMetric)

from .base_analysis import AnalyzerBase

metric_attitude_msg_valid_p1_time = AlwaysTrueMetric(
    'attitude_msg_valid_p1_time',
    "All attitude messages should have a valid P1 time. If this check is skipped, no attitude messages were received.",
    is_required=True,
)

metric_attitude_msg_period = StatsMetric(
    'attitude_msg_period',
    "The time between attitude messages should be close to the attitude's expected rate.",
    # The actual thresholds are set based on the device in `configure_metrics()`.
    is_logged=True,
)

metric_attitude_valid = AlwaysTrueMetric(
    'attitude_valid',
    'All attitudes should be valid after the navigation engine initializes (i.e., the navigation engine should not '
    'reset once operating).',
)

metric_attitude_non_nan = AlwaysTrueMetric(
    'attitude_non_nan',
    'ypr_deg and baseline_distance_m should be non-nan if the output is marked valid.',
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
        metric_attitude_msg_period.max_threshold = nominal_period_sec + max_tolerance_sec
        metric_attitude_msg_period.min_threshold = nominal_period_sec - max_tolerance_sec
        metric_attitude_msg_period.max_cdf_thresholds = [
            CdfThreshold(50, nominal_period_sec + percentile_50_tolerance_sec)]
        metric_attitude_msg_period.min_cdf_thresholds = [
            CdfThreshold(50, nominal_period_sec - percentile_50_tolerance_sec)]

        if not params.check_position:
            pass

        # Disable some checks if the test is sending reset commands.
        if params.has_resets:
            metric_attitude_valid.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


class HeadingAnalyzer(AnalyzerBase):
    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        self.last_p1_time: Optional[Timestamp] = None
        self.is_valid = False

    def update(self, msg: MessageWithBytesTuple):
        if not self.env_args.HITL_BUILD_TYPE.has_attitude():
            return

        _, payload, _ = msg
        if isinstance(payload, GNSSAttitudeOutput):
            metric_attitude_msg_valid_p1_time.check(bool(payload.get_p1_time()))
            # Skip further processing if no P1 time is present.
            if not payload.get_p1_time():
                return

            if self.last_p1_time is not None:
                time_diff = payload.get_p1_time().seconds - self.last_p1_time.seconds
                metric_attitude_msg_period.check(time_diff)
            self.last_p1_time = payload.get_p1_time()

            if payload.solution_type == SolutionType.Invalid:
                if self.is_valid:
                    metric_attitude_valid.check(False)
                return
            metric_attitude_valid.check(True)

            failure_context = ''
            # Note that gyro_std_rps and accel_std_mps2 are allowed to be NaN.
            if math.isnan(payload.ypr_deg[0]):
                failure_context = 'yaw had a NaN value.'
            elif math.isnan(payload.baseline_distance_m):
                failure_context = 'baseline distance had a NaN value.'
            metric_attitude_non_nan.check(len(failure_context) == 0, failure_context)
