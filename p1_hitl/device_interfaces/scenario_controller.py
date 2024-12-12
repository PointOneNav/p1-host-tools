import json
import logging
import time
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import List, NamedTuple, Optional

from fusion_engine_client.messages import ResetRequest

from p1_hitl.defs import TEST_EVENT_FILE, HitlEnvArgs, TestType
from p1_hitl.metric_analysis.metrics import (MetricController, Timestamp,
                                             custom_json)
from p1_runner.device_interface import DeviceInterface

logger = logging.getLogger('point_one.hitl.scenario_controller')


class EventType(Enum):
    RESET = auto()


class ResetType(Enum):
    HOT = auto()
    WARM = auto()
    COLD = auto()


@dataclass(frozen=True)
class EventEntry:
    timestamp: Timestamp
    event_type: EventType
    description: str


class ScenarioBase(ABC):
    @abstractmethod
    def __init__(self, env_args: HitlEnvArgs, device_interface: DeviceInterface):
        self.env_args = env_args
        self.device_interface = device_interface

    @abstractmethod
    def update_controller(self) -> list[EventEntry]:
        ...


class ResetScenario(ScenarioBase):
    def __init__(self, env_args: HitlEnvArgs, device_interface: DeviceInterface):
        super().__init__(env_args, device_interface)
        self.last_restart: Optional[ResetType] = None
        self.last_restart_time = time.monotonic()
        logger.info(f'Running reset scenario.')

    def _send_reset(self, reset_type: ResetType) -> EventEntry:
        reset_mask = {
            ResetType.HOT: ResetRequest.HOT_START,
            ResetType.WARM: ResetRequest.WARM_START,
            ResetType.COLD: ResetRequest.COLD_START,
        }[reset_type]
        logger.info(f'Sending {reset_type.name} restart.')
        msg = ResetRequest(reset_mask)
        self.device_interface.send_message(msg)
        self.last_restart = reset_type
        self.last_restart_time = time.monotonic()
        current_timestamp = deepcopy(MetricController._current_time)
        return EventEntry(
            timestamp=current_timestamp,
            event_type=EventType.RESET,
            description=reset_type.name
        )

    def update_controller(self) -> list[EventEntry]:
        '''!
        This controller does the following:
        1. Wait a minute for the DUT to navigate.
        2. Send a hot start reset.
        3. Wait 10 seconds DUT to navigate.
        4. Send a warm start reset.
        5. Wait 60 seconds DUT to navigate.
        6. Send a cold start reset.
        7. Let the DUT run normally for the remainder of the test duration (~2.5 minutes).
        '''
        elapsed = time.monotonic() - self.last_restart_time
        events: list[EventEntry] = []
        # Hot start a minute into run.
        if self.last_restart is None:
            if elapsed > 60:
                events.append(self._send_reset(ResetType.HOT))
        # Warm start 10 seconds after hot start.
        elif self.last_restart is ResetType.HOT:
            if elapsed > 10:
                events.append(self._send_reset(ResetType.WARM))
        # Cold start a minute after warm start.
        elif self.last_restart is ResetType.WARM:
            if elapsed > 60:
                events.append(self._send_reset(ResetType.COLD))

        return events


SCENARIO_MAP = {
    TestType.RESET_TESTS: ResetScenario
}


class ScenarioController:

    def __init__(self, env_args: HitlEnvArgs, log_dir: Path,
                 device_interface: Optional[DeviceInterface] = None) -> None:
        self.env_args = env_args
        self.scenario: Optional[ScenarioBase] = None
        self.log_dir = log_dir
        self.playback_events: List[EventEntry] = []
        self.event_log: List[EventEntry] = []
        test_type = self.env_args.get_selected_test_type()
        event_log_file = log_dir / TEST_EVENT_FILE
        if device_interface is not None:
            assert device_interface is not None
            if test_type in SCENARIO_MAP:
                self.event_log_fd = open(event_log_file, 'w')
                self.scenario = SCENARIO_MAP[test_type](env_args, device_interface)
            else:
                logger.info(f'No scenario controller loaded.')
        else:
            if event_log_file.exists():
                event_json_data = json.load(open(event_log_file))
                self.playback_events = [
                    EventEntry(
                        timestamp=Timestamp(**event['timestamp']),
                        event_type=EventType[event['event_type']],
                        description=event['description']
                    )
                    for event in event_json_data
                ]
                logger.info(f'Loaded {len(self.playback_events)} events for playback.')
            elif test_type in SCENARIO_MAP:
                logger.error('No events to playback.')

    def update_controller(self) -> list[EventEntry]:
        if len(self.playback_events) > 0:
            current_p1_time = MetricController._current_time.p1_time
            events: List[EventEntry] = []
            # Use p1time to replay when events should trigger.
            if current_p1_time is not None:
                while len(self.playback_events) > 0:
                    event_time = self.playback_events[0].timestamp.p1_time
                    if event_time is None or event_time > current_p1_time:
                        break
                    logger.info('Playing back event: ' + str(self.playback_events[0]))

                    events.append(self.playback_events.pop(0))
            return events
        elif self.scenario is not None:
            events = self.scenario.update_controller()
            # Log events.
            if len(events) > 0:
                self.event_log += events
                event_log_file = self.log_dir / TEST_EVENT_FILE
                json.dump(self.event_log, open(event_log_file, 'w'), indent=2, default=custom_json)
            return events
        else:
            return []
