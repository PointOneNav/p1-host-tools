from abc import ABC, abstractmethod

from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.device_interfaces.scenario_controller import EventEntry


class AnalyzerBase(ABC):
    def __init__(self, env_args: HitlEnvArgs):
        self.env_args = env_args
        self.params = env_args.get_selected_test_type().get_test_params()

    @abstractmethod
    def update(self, msg: MessageWithBytesTuple):
        ...

    def on_event(self, event: EventEntry):
        pass

    def stop(self):
        pass
