from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from p1_hitl.defs import HiltEnvArgs
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.devices_config import DeviceConfig


class DeviceInitBase(ABC):
    @staticmethod
    @abstractmethod
    def get_device_config(args: HiltEnvArgs) -> Optional[DeviceConfig]:
        ...

    @staticmethod
    @abstractmethod
    def init_device(config: DeviceConfig, build_info: Dict[str, Any]) -> Optional[DeviceInterface]:
        ...
