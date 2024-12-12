import logging
from typing import Optional

from device_interfaces.scenario_controller import EventEntry, ResetType
from fusion_engine_client.messages import (CommandResponseMessage, PoseMessage,
                                           SolutionType)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.device_interfaces.scenario_controller import EventType
from p1_hitl.metric_analysis.metrics import (MaxElapsedTimeMetric,
                                             MetricController, TimeSource)

from .base_analysis import AnalyzerBase

logger = logging.getLogger('point_one.hitl.analysis.ttff')

metric_time_to_reset_response = MaxElapsedTimeMetric(
    'time_to_reset_response',
    'Time between test scenario reset event and reset response from DUT in seconds.',
    time_source=TimeSource.P1,
    max_elapsed_time_sec=2.0
)
metric_time_between_reset_and_invalid = MaxElapsedTimeMetric(
    'time_between_reset_and_invalid',
    'Time between test scenario reset event and DUT pose going invalid in seconds.',
    time_source=TimeSource.P1,
    max_elapsed_time_sec=2.0
)

RESET_NAMES = {k: k.name.lower() for k in ResetType}
RECOVERY_TIMES = {
    ResetType.HOT: 10.0,
    ResetType.WARM: 60,
    ResetType.COLD: 60,
}
metric_time_between_invalid_and_valid = {
    r: MaxElapsedTimeMetric(
        f'time_between_{RESET_NAMES[r]}_invalid_and_valid',
        f'Time for pose to recover after going invalid from a {RESET_NAMES[r]} reset in seconds.',
        time_source=TimeSource.P1,
        max_elapsed_time_sec=RECOVERY_TIMES[r]
    )
    for r in RESET_NAMES
}
metric_time_between_invalid_and_fixed = {
    r: MaxElapsedTimeMetric(
        f'time_between_{RESET_NAMES[r]}_invalid_and_fixed',
        f'Time for pose to fix after going invalid from a {RESET_NAMES[r]} reset in seconds.',
        time_source=TimeSource.P1,
        max_elapsed_time_sec=RECOVERY_TIMES[r]
    )
    for r in RESET_NAMES
}


def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.get_selected_test_type().get_test_params()
    reset_ttff_metrics = MetricController.get_metrics_in_this_file()
    if not params.has_resets:
        for metric in reset_ttff_metrics:
            metric.is_disabled = True
    else:
        if not params.has_corrections:
            for metric in metric_time_between_invalid_and_fixed.values():
                metric.is_disabled = True


MetricController.register_environment_config_customizations(configure_metrics)


class ResetTTFFAnalyzer(AnalyzerBase):
    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        self.last_reset_event: Optional[EventEntry] = None
        self.invalid_seen_for_reset = False

    def update(self, msg: MessageWithBytesTuple):
        if not self.params.has_resets or self.last_reset_event is None:
            return

        last_reset_type = ResetType[self.last_reset_event.description]

        _, payload, _ = msg
        if isinstance(payload, CommandResponseMessage):
            metric_time_to_reset_response.stop()
        elif isinstance(payload, PoseMessage):
            if payload.solution_type == SolutionType.Invalid and not self.invalid_seen_for_reset:
                metric_time_between_reset_and_invalid.stop()
                metric_time_between_invalid_and_valid[last_reset_type].start()
                metric_time_between_invalid_and_fixed[last_reset_type].start()
                self.invalid_seen_for_reset = True
            elif self.invalid_seen_for_reset:
                if payload.solution_type != SolutionType.Invalid:
                    metric_time_between_invalid_and_valid[last_reset_type].stop()
                    if payload.solution_type == SolutionType.RTKFixed:
                        elapsed = metric_time_between_invalid_and_fixed[last_reset_type].stop()
                        if elapsed is not None:
                            logger.info(f'{RESET_NAMES[last_reset_type]} reset recovered RTKFixed after {elapsed:0.1f}s')

    def on_event(self, event: EventEntry):
        if event.event_type is EventType.RESET:
            self.last_reset_event = event
            self.invalid_seen_for_reset = False
            metric_time_to_reset_response.start()
            metric_time_between_reset_and_invalid.start()
