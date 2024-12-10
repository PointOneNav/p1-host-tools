from abc import ABC, abstractmethod

from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs
from p1_hitl.device_interfaces.scenario_controller import EventEntry


class AnalyzerBase(ABC):
    def configure(self, env_args: HitlEnvArgs):
        pass

    @abstractmethod
    def update(self, msg: MessageWithBytesTuple):
        ...

    def on_event(self, event: EventEntry):
        pass
