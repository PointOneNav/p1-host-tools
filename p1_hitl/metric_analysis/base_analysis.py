from abc import ABC, abstractmethod

from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs


class AnalyzerBase(ABC):
    def configure(self, env_args: HitlEnvArgs):
        pass

    @abstractmethod
    def update(self, msg: MessageWithBytesTuple):
        ...
