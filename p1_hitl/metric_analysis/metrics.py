'''!
Framework for specifying HITL Metrics

Top level design:
  MetricController - is used to manage the global controls and state of the test being performed.
  Check classes - These checks are declared in analysis code to specify the testing requirements. These are akin to
                  asserts in a unit test.

The check classes are expected to be customized at runtime based on the HitlEnvArgs and the TestParams derived from the
TestType.
'''

import inspect
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import IntEnum, auto
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, Dict, List, NamedTuple, Optional

from fusion_engine_client.messages import MessagePayload
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import HitlEnvArgs

logger = logging.getLogger('point_one.hitl.metrics')

# Format for message time log:
# [u32 host test time elapsed in milliseconds][u32 message sequence number]
# The sequence number is probably not necessary, but can crosscheck that the decoder operation is consistent.
_MSG_TIME_LOG_FORMAT = '<II'
MSG_TIME_LOG_FILENAME = 'msg_times.bin'

# Format for metric log:
# [u32 test time elapsed in milliseconds (host if available, or device for playback)][f64 metric value]
_METRIC_LOG_FORMAT = '<Id'


class FatalMetricException(Exception):
    '''!
    Exception to indicate a test with `is_fatal==True` failed.
    '''

    def __init__(self, test_name) -> None:
        self.test_name = test_name


class TimeSource(IntEnum):
    '''!
    Indicates which time sources to use when checking for elapsed time.
    '''
    # Only consider the host time
    HOST = auto()
    # Only consider the P1 and system timestamps from device FE messages
    DEVICE = auto()
    # Use all available time sources
    ANY = auto()


class CodeLocation(NamedTuple):
    '''!
    Tracks where in the code base a test metric was declared.
    '''
    file: Path
    line: int


@dataclass
class Timestamp:
    '''!
    Captures when during the HITL test something occurred.
    '''

    host_time: Optional[float]
    p1_time: Optional[float]
    system_time: Optional[float]

    def get_max_elapsed(self, previous_time: 'Timestamp', time_source: TimeSource) -> float:
        '''!
        Gets the time that elapsed "self - previous_time".

        If multiple "types" of timestamps are applicable, find the elapsed time in each time base and take the greatest
        value.

        Makes no attempt to map between time based, though that might be an approach for future improvements.

        @param previous_time - The previous timestamp to get the elapsed time from
        @param time_source - Which time sources should be considered for the comparison. See @ref TimeSource.

        @return The elapsed time in seconds. Returns `0` if one or both of the timestamps had no valid data for the
                specified time source.
        '''
        elapsed = 0

        def _update_max_elapsed(a, b, cur_max_elapsed) -> float:
            if a is not None and b is not None:
                new_elapsed = a - b
                if cur_max_elapsed is None or new_elapsed > cur_max_elapsed:
                    return new_elapsed

            return cur_max_elapsed

        if time_source is TimeSource.HOST or time_source is TimeSource.ANY:
            elapsed = _update_max_elapsed(self.host_time, previous_time.host_time, elapsed)

        if time_source is TimeSource.DEVICE or time_source is TimeSource.ANY:
            elapsed = _update_max_elapsed(self.system_time, previous_time.system_time, elapsed)
            elapsed = _update_max_elapsed(self.p1_time, previous_time.p1_time, elapsed)

        return elapsed


class MetricController:
    '''!
    Class for controlling global metric testing state.

    @warning This class is not multi-thread safe. This is easy to see with its control of `_current_time`. No metrics
             should be updated while calls to this class are being done.
    '''
    # Metrics will add themselves to this list.
    _metrics: ClassVar[Dict[str, 'Metric']] = {}
    # Set of callbacks to customize metric configurations at runtime.
    _config_customization_callbacks: ClassVar[List[Callable[[HitlEnvArgs], None]]] = []
    # Logging settings
    _log_dir: ClassVar[Optional[Path]] = None
    _log_msg_times: ClassVar[bool] = False
    _log_metric_values: ClassVar[bool] = False
    _time_log_fd: ClassVar[Optional[BinaryIO]] = None
    # Global state for providing time information to metrics. This is for log
    # entries, failure times, and tracking elapsed time metrics.
    _start_time: ClassVar[Timestamp] = Timestamp(None, None, None)
    _current_time: ClassVar[Timestamp] = Timestamp(None, None, None)

    @classmethod
    def enable_logging(cls, log_dir: Path, log_msg_times: bool, log_metric_values: bool):
        '''!
        @param log_dir - Directory to write log files to.
        @param log_msg_times - Should the host time of each FE message be logged.
        @param log_metric_values - Should the value of each metric with `not_logged==False` be logged.
        '''
        cls._log_dir = log_dir
        cls._log_msg_times = log_msg_times
        cls._log_metric_values = log_metric_values

    @classmethod
    def update_host_time(cls):
        '''!
        Update the global concept of current host time.

        This is used to set any checks that rely on a host TimeSource.
        This should only be called for realtime operation, and not called if playing back a log file.
        '''
        cls._current_time.host_time = time.time()
        if cls._start_time.host_time is None:
            cls._start_time.host_time = cls._current_time.host_time

        # Check metrics triggered by elapsed host time.
        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._time_elapsed()

    @classmethod
    def update_timestamps(cls, msg: MessageWithBytesTuple):
        '''!
        Update the global concept of current device time.

        This is used to set any checks that rely on a device TimeSource.
        This should be called for each message decoded from the device.
        '''
        header, payload, _ = msg
        if isinstance(payload, MessagePayload):
            p1_time = payload.get_p1_time()
            if p1_time is not None:
                cls._current_time.p1_time = p1_time.seconds
                if cls._start_time.p1_time is None:
                    cls._start_time.p1_time = p1_time.seconds
            system_time = payload.get_system_time_sec()
            if system_time is not None:
                cls._current_time.system_time = system_time
                if cls._start_time.system_time is None:
                    cls._start_time.system_time = system_time

        # Only log host times when update_host_time() is being called.
        if cls._current_time.host_time is not None and cls._log_dir is not None and cls._log_msg_times:
            if cls._time_log_fd is None:
                file_path = cls._log_dir / MSG_TIME_LOG_FILENAME
                cls._time_log_fd = open(file_path, 'wb')

            test_time_millis = round(cls._current_time.get_max_elapsed(cls._start_time, TimeSource.HOST) * 1000.0)
            cls._time_log_fd.write(struct.pack(_MSG_TIME_LOG_FORMAT, test_time_millis, header.sequence_number))

        # Check metrics triggered by elapsed device time.
        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._time_elapsed()

    @classmethod
    def register_environment_config_customizations(cls, callback: Callable[[HitlEnvArgs], None]):
        '''!
        Register a callback to run when @ref apply_environment_config_customizations() is called.

        These are meant to specify metric customizations to apply bask on the runtime test environment.
        '''
        cls._config_customization_callbacks.append(callback)

    @classmethod
    def apply_environment_config_customizations(cls, env_args: HitlEnvArgs):
        '''!
        Run callbacks set with @ref register_environment_config_customizations().

        This is meant to run once on test initialization.
        '''
        for callback in cls._config_customization_callbacks:
            callback(env_args)

    @classmethod
    def finalize(cls):
        '''!
        Call metric `_finalize()` functions for processing metrics that are only checked on test completion.
        '''
        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._finalize()
                if not metric.was_checked and metric.is_required:
                    metric.failure_time = cls._current_time
                    metric.failure_context = 'not_checked'

    @classmethod
    def get_metrics_in_this_file(cls):
        '''!
        Utility function to return a list of metrics that were declared in the same file as this function's caller.

        For example, if file "foo.py" declared 2 metrics "test1" and "test2". These objects would be returned if
        MetricController.get_metrics_in_this_file() were also called somewhere in foo.py.
        '''
        # Go up 1 frames to the caller
        frame = inspect.stack()[1]
        caller_path = Path(frame.filename)
        return [m for m in cls._metrics.values() if m.code_location.file == caller_path]

    @classmethod
    def generate_report(cls) -> Dict[str, Any]:
        '''!
        Generate dict with the configuration and results from all the active metrics.
        '''
        results = {}
        for name, metric in cls._metrics.items():
            if not metric.is_disabled:
                results[name] = asdict(metric)
        return {'results': results, 'test_start': cls._start_time}


@dataclass
class Metric:
    ############# Metric configuration #############
    name: str
    description: str

    # Using field with kw_only allows child classes to have values without defaults:
    # https://stackoverflow.com/a/58525728

    # Abort test immediately if metric fails.
    is_fatal: bool = field(default=False, kw_only=True)
    # The metric is considered failing if it is not checked during test.
    is_required: bool = field(default=False, kw_only=True)
    # The metric should be ignored.
    is_disabled: bool = field(default=False, kw_only=True)
    # Should this metric be exempt from logging
    not_logged: bool = field(default=False, kw_only=True)

    ## Set automatically, do not set manually. ##
    # Context on where the metric was declared.
    code_location: CodeLocation = field(default=CodeLocation(Path(), 0), init=False)

    ############# Metric state #############
    # If set, the time of the metrics first failure.
    failure_time: Optional[Timestamp] = field(default=None, init=False)
    # For metrics with more complicated trigger, additional context on the cause of the failure.
    failure_context: Optional[str] = field(default=None, init=False)
    # Has this metric been checked.
    was_checked: bool = field(default=False, init=False)

    def __post_init__(self):
        if self.name in MetricController._metrics:
            raise KeyError(f'Duplicate metrics named "{self.name}"')
        else:
            MetricController._metrics[self.name] = self

        # Go back through stack __post_init__ and __init__ function calls. May
        # be multiple __post_init__ from inheritance.
        next = False
        for frame in inspect.stack():
            if next:
                self.code_location = CodeLocation(Path(frame.filename), frame.lineno)
                break
            elif frame.function == '__init__':
                next = True

        # Have to declare here so it's not included as a field for serialization.
        self._log_fd: Optional[BinaryIO] = None

    def __log(self, value: float):
        if not self.is_disabled and MetricController._log_metric_values and MetricController._log_dir:
            if self._log_fd is None:
                file_path = MetricController._log_dir / (self.name + '.bin')
                self._log_fd = open(file_path, 'wb')

            test_time_millis = round(
                MetricController._current_time.get_max_elapsed(
                    MetricController._start_time,
                    TimeSource.ANY) * 1000.0)
            self._log_fd.write(struct.pack(_METRIC_LOG_FORMAT, test_time_millis, value))

    def _update_status(self, value: float, is_failure: bool, not_logged=False):
        if self.is_disabled:
            return
        if not self.not_logged and not not_logged:
            self.__log(value)

        self._update_failure(is_failure, context=str(value))

    def _update_failure(self, is_failure: bool, context=None):
        self.was_checked = True
        if self.is_disabled:
            return
        if is_failure and self.failure_time is None:
            logger.info(f'Failure {self.name}: {context}')
            self.failure_time = MetricController._current_time
            self.failure_context = context
            if self.is_fatal:
                raise FatalMetricException(self.name)

    def _finalize(self):
        pass

    def _time_elapsed(self):
        pass


@dataclass
class MaxValue(Metric):
    threshold: float

    def check(self, value: float):
        self._update_status(value, value > self.threshold)


@dataclass
class MaxElapsedTime(Metric):
    time_source: TimeSource
    max_time_to_first_check_sec: Optional[float] = None
    max_time_between_checks_sec: Optional[float] = None

    __last_time = None

    def check(self) -> Optional[float]:
        elapsed = None
        if isinstance(self.__last_time, Timestamp):
            elapsed = MetricController._current_time.get_max_elapsed(self.__last_time, self.time_source)
            # Failures are updated in _time_elapsed() function.
            self._update_status(elapsed, False)
        self.__last_time = MetricController._current_time
        return elapsed

    def _time_elapsed(self):
        if isinstance(self.__last_time, Timestamp):
            if self.max_time_between_checks_sec is not None:
                elapsed = MetricController._current_time.get_max_elapsed(self.__last_time, self.time_source)
                self._update_failure(elapsed > self.max_time_between_checks_sec, 'max_time_between_checks')
        else:
            if self.max_time_to_first_check_sec is not None:
                elapsed = MetricController._current_time.get_max_elapsed(MetricController._start_time, self.time_source)
                self._update_failure(elapsed > self.max_time_to_first_check_sec, 'max_time_to_first_check')


class CdfThreshold(NamedTuple):
    percentile: float
    threshold: float


@dataclass
class StatsCheck(Metric):
    '''!
    Any combination of the thresholds may be set. The check will fail if any of
    the thresholds are violated.
    '''
    # Check fails if any value is above this.
    max_threshold: Optional[float] = None
    # Check fails if any value is below this.
    min_threshold: Optional[float] = None
    # Lists of percentiles to check. The check fails if any entry fails. The
    # check is specified as (percentile, threshold). Check fails if the observed
    # percentile at the end of the run is larger then the specified threshold.
    # For example (50, 1) means that the check will fail if the median is larger
    # then 1.
    max_cdf_thresholds: List[CdfThreshold] = field(default_factory=list)
    # Lists of percentiles to check. The check fails if any entry fails. The
    # check is specified as (percentile, threshold). Check fails if the observed
    # percentile at the end of the run is larger then the specified threshold.
    # For example (50, 1) means that the check will fail if the median is larger
    # then 1.
    min_cdf_thresholds: List[CdfThreshold] = field(default_factory=list)
    min_values_for_cdf_check: int = 100

    __cdf_ratios = {}

    def __post_init__(self):
        super().__post_init__()
        thresholds = set(k.threshold for k in self.max_cdf_thresholds)
        thresholds.update(k.threshold for k in self.min_cdf_thresholds)
        for threshold in thresholds:
            self.__cdf_ratios[threshold] = [0, 0]

    def check(self, value: float):
        if not self.is_disabled:
            # Failures are updated below and in _finalize().
            self._update_status(value, False)
            if self.max_threshold is not None:
                self._update_failure(value > self.max_threshold, 'max_threshold')
            if self.min_threshold is not None:
                self._update_failure(value < self.min_threshold, 'min_threshold')
            for k, v in self.__cdf_ratios.items():
                # Not counting values == threshold.
                if value < k:
                    v[0] += 1

                if value != k:
                    v[1] += 1

    def _finalize(self):
        if not self.is_disabled and len(self.__cdf_ratios) > 0:
            failures = []

            empirical_percentiles = {}
            for threshold, counts in self.__cdf_ratios.items():
                smaller_count, total = counts
                if total > self.min_values_for_cdf_check:
                    empirical_percentiles[threshold] = smaller_count / total * 100.0

            for percentile, threshold in self.max_cdf_thresholds:
                if threshold in empirical_percentiles:
                    if empirical_percentiles[threshold] < percentile:
                        failures.append(f'{percentile}_percentile_above_{threshold}')

            for percentile, threshold in self.min_cdf_thresholds:
                if threshold in empirical_percentiles:
                    if empirical_percentiles[threshold] > percentile:
                        failures.append(f'{percentile}_percentile_below_{threshold}')

            if len(failures) > 0:
                self._update_failure(True, ','.join(failures))


@dataclass
class PercentTrue(Metric):
    # Fail if less than this percent of checks are true.
    min_percent_true: float
    min_values_for_check: int = 100

    __true_count = 0
    __total_count = 0

    def check(self, value: bool):
        if not self.is_disabled:
            if value:
                self.__true_count += 1
            self.__total_count += 1

    def _finalize(self):
        if not self.is_disabled and self.__total_count >= self.min_values_for_check:
            measured_percent = self.__true_count / self.__total_count * 100.0
            self._update_status(measured_percent, measured_percent < self.min_percent_true, True)


@dataclass
class MinValue(Metric):
    threshold: float

    def check(self, value: float):
        self._update_status(value, value < self.threshold)


@dataclass
class EqualValue(Metric):
    threshold: float

    def check(self, value: float):
        self._update_status(value, value != self.threshold)


@dataclass
class IsTrue(Metric):
    def check(self, value: bool):
        self._update_status(value, not value)


def _main():
    test1 = MaxValue('test1', 'test1 description', 10)
    test2 = MaxValue('test2', 'test2 description', 10)
    test3 = StatsCheck(
        'test3', 'stat test',
        max_cdf_thresholds=[
            CdfThreshold(10, 11),
            CdfThreshold(50, 49)
        ],
        min_cdf_thresholds=[CdfThreshold(50, 51)],
        min_values_for_cdf_check=10)
    print(MetricController._metrics)

    for i in range(100):
        MetricController.update_host_time()
        test3.check(i)
    test3._finalize()
    print(test3)


if __name__ == '__main__':
    _main()
