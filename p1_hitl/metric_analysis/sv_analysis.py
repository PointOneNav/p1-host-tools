import math
from typing import Optional

from fusion_engine_client.messages import GNSSSatelliteMessage, SatelliteType
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.metric_analysis.metrics import AlwaysTrueMetric, MetricController

from .base_analysis import AnalyzerBase

metric_glonass_tracked = AlwaysTrueMetric(
    'glonass_tracked',
    "Glonass satellites are tracked for at least a portion of the test.",
    is_required=True,
)

metric_galileo_tracked = AlwaysTrueMetric(
    'galileo_tracked',
    "Galileo satellites are tracked for at least a portion of the test.",
    is_required=True,
)

metric_beidou_tracked = AlwaysTrueMetric(
    'beidou_tracked',
    "BeiDou satellites are tracked for at least a portion of the test.",
    is_required=True,
)

TRACKED_METRIC_MAP = {
    SatelliteType.GLONASS: metric_glonass_tracked,
    SatelliteType.GALILEO: metric_galileo_tracked,
    SatelliteType.BEIDOU: metric_beidou_tracked,
}


def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.get_selected_test_type().get_test_params()
    sv_metrics = MetricController.get_metrics_in_this_file()
    # Only check SV info when positioning was expected.
    if not params.check_position:
        for metric in sv_metrics:
            metric.is_disabled = True
    else:
        # Don't expect Glonass on LG69T based platforms.
        if env_args.HITL_BUILD_TYPE.is_lg69t() or env_args.HITL_BUILD_TYPE in [
                DeviceType.BMW_MOTO, DeviceType.AMAZON_FLEETEDGE_V1, DeviceType.ZIPLINE]:
            metric_glonass_tracked.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


class SVAnalyzer(AnalyzerBase):
    def update(self, msg: MessageWithBytesTuple):
        if self.env_args.HITL_BUILD_TYPE.is_gnss_only():
            return

        _, payload, _ = msg
        if isinstance(payload, GNSSSatelliteMessage):
            for sv in payload.svs:
                if sv.system in TRACKED_METRIC_MAP:
                    TRACKED_METRIC_MAP[sv.system].check(True)
