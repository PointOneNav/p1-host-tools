import math
from typing import Optional

from fusion_engine_client.messages import IMUOutput, Timestamp
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric, CdfThreshold,
                                             MetricController, StatsMetric)

from .base_analysis import AnalyzerBase

metric_imu_msg_valid_p1_time = AlwaysTrueMetric(
    'imu_msg_valid_p1_time',
    "All IMU messages should have a valid P1 time. If this check is skipped, no IMU messages were received.",
    is_required=True,
)

metric_imu_msg_period = StatsMetric(
    'imu_msg_period',
    "The time between IMU messages should be close to the IMU's expected rate.",
    # The actual thresholds are set based on the device in `configure_metrics()`.
    is_logged=True,
)

metric_imu_msg_non_nan = AlwaysTrueMetric(
    'imu_msg_non_nan',
    'accel_mps2 and gyro_rps should be non-nan.',
)


def configure_metrics(env_args: HitlEnvArgs):
    imu_metrics = MetricController.get_metrics_in_this_file()
    # Don't check IMU for GNSS only devices.
    if env_args.HITL_BUILD_TYPE.is_gnss_only():
        for metric in imu_metrics:
            metric.is_disabled = True
    else:
        # Atlas has 100Hz IMU rate.
        if env_args.HITL_BUILD_TYPE in [DeviceType.ATLAS]:
            nominal_period = 0.01
            metric_imu_msg_period.max_threshold = nominal_period + 0.005
            metric_imu_msg_period.min_threshold = nominal_period - 0.005
            metric_imu_msg_period.max_cdf_thresholds = [CdfThreshold(50, nominal_period + 0.001)]
            metric_imu_msg_period.min_cdf_thresholds = [CdfThreshold(50, nominal_period - 0.001)]
        # LG69T devices have 26Hz IMU rate.
        elif env_args.HITL_BUILD_TYPE.is_lg69t() or \
                env_args.HITL_BUILD_TYPE in [DeviceType.AMAZON, DeviceType.BMW_MOTO, DeviceType.ZIPLINE]:
            nominal_period = 1.0 / 26.0
            metric_imu_msg_period.max_threshold = nominal_period + 0.01
            metric_imu_msg_period.min_threshold = nominal_period - 0.01
            metric_imu_msg_period.max_cdf_thresholds = [CdfThreshold(50, nominal_period + 0.001)]
            metric_imu_msg_period.min_cdf_thresholds = [CdfThreshold(50, nominal_period - 0.001)]
        else:
            raise NotImplementedError(f'IMU rate not configured for {env_args.HITL_BUILD_TYPE.name}.')


MetricController.register_environment_config_customizations(configure_metrics)


class IMUAnalyzer(AnalyzerBase):
    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        self.last_p1_time: Optional[Timestamp] = None

    def update(self, msg: MessageWithBytesTuple):
        if self.env_args.HITL_BUILD_TYPE.is_gnss_only():
            return

        _, payload, _ = msg
        if isinstance(payload, IMUOutput):
            metric_imu_msg_valid_p1_time.check(bool(payload.get_p1_time()))
            # Skip further processing if no P1 time is present.
            if not payload.get_p1_time():
                return

            if self.last_p1_time is not None:
                time_diff = payload.get_p1_time().seconds - self.last_p1_time.seconds
                metric_imu_msg_period.check(time_diff)
            self.last_p1_time = payload.get_p1_time()

            any_nan = False
            failure_context = ''
            # Note that gyro_std_rps and accel_std_mps2 are allowed to be NaN.
            fields = {'accel_mps2', 'gyro_rps'}
            for field in fields:
                any_nan = any(math.isnan(v) for v in vars(payload)[field])
                if any_nan:
                    failure_context = f'{field} had a NaN value.'
                    break
            metric_imu_msg_non_nan.check(not any_nan, failure_context)
