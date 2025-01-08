from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from p1_hitl.defs import HitlEnvArgs
from p1_runner.device_interface import DeviceInterface
from p1_test_automation.devices_config import DeviceConfig


class HitlDeviceInterfaceBase(ABC):
    @staticmethod
    @abstractmethod
    def get_device_config(args: HitlEnvArgs) -> Optional[DeviceConfig]:
        ...

    @abstractmethod
    def __init__(self, config: DeviceConfig, env_args: HitlEnvArgs):
        ...

    @abstractmethod
    def init_device(self, build_info: Dict[str, Any], skip_reset=False,
                    skip_corrections=False) -> Optional[DeviceInterface]:
        ...

    @abstractmethod
    def shutdown_device(self, tests_passed: bool, output_dir: Path):
        ...
