'''!
Framework for specifying HITL Metrics

# Top level design
  MetricController - is used to manage the global controls and state of the test being performed.
  Check classes - These checks are declared in analysis code to specify the testing requirements. These are akin to
                  asserts in a unit test.

# Time Stamping

There are 3 time bases:

host - The monotonic time of the host running the metrics analysis when the last FE message was received. This is only
       intended to be set if the metrics are being evaluated in realtime and not from an existing file log.
p1 - The P1Time associated with a message received from the device. This is a monotonically increasing value that is
     kept rate locked to GPS time when possible.
system - The system timestamp associated with a message received from the device. Typically, this is the underlying OS
         or MCU clock. It has no defined relationship to any other time base.

The current time is the set of the host time the latest FE message was received, the device timestamps from the latest
message, and the last timestamp received for any time bases that aren't updated. This means that for messages with a
P1Time, the system time will be lagging behind.

Metrics and logs check the "elapsed time" from these time sources. Currently, no effort is made to map between the,
source types. The elapsed time is only computed with respect to a single time base.

# Runtime Metric Configuration

The check classes are expected to be customized at runtime based on the HitlEnvArgs and the TestParams derived from the
TestType.

To be consistent in when this is being performed, files that declare metrics should add a callback to @ref
MetricController.register_environment_config_customizations() that implement the logic to update values based on the
environment.
'''

import inspect
import logging
import math
import struct
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import IntEnum, auto
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, Dict, List, NamedTuple, Optional

from fusion_engine_client.messages import MessagePayload
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import MSG_TIME_LOG_FILENAME, HitlEnvArgs

logger = logging.getLogger('point_one.hitl.metrics')

# Format for message time log:
# [u32 host test time elapsed in milliseconds][u32 message sequence number]
# The sequence number is probably not necessary, but can crosscheck that the decoder operation is consistent.
_MSG_TIME_LOG_FORMAT = '<II'

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
    # Only consider the P1 timestamps from device FE messages
    P1 = auto()
    # Only consider system timestamps from device FE messages
    SYSTEM = auto()


class CodeLocation(NamedTuple):
    '''!
    Tracks where in the code base a test metric was declared.
    '''
    file: Path
    line: int

    def __str__(self) -> str:
        return f'{self.file}:{self.line}'


@dataclass
class Timestamp:
    '''!
    Captures when during the HITL test something occurred.
    '''

    host_time: Optional[float]
    p1_time: Optional[float]
    system_time: Optional[float]

    def get_elapsed(self, previous_time: 'Timestamp', time_source: TimeSource) -> Optional[float]:
        '''!
        Gets the time that elapsed "self - previous_time".

        Makes no attempt to map between time bases, though that might be an approach for future improvements.

        @param previous_time - The previous timestamp to get the elapsed time from
        @param time_source - The time base to get the elapsed time for

        @return The elapsed time in seconds. Returns `None` if one or both of the timestamps had no valid data for the
                specified time source.
        '''
        current, previous = {
            TimeSource.HOST: (self.host_time, previous_time.host_time),
            TimeSource.P1: (self.p1_time, previous_time.p1_time),
            TimeSource.SYSTEM: (self.system_time, previous_time.system_time)
        }[time_source]
        if current is not None and previous is not None:
            return current - previous
        else:
            return None

    def get_max_elapsed(self, previous_time: 'Timestamp') -> Optional[float]:
        '''!
        Gets the time that elapsed "self - previous_time".

        If multiple "types" of timestamps are applicable, find the elapsed time in each time base and take the greatest
        value.

        Makes no attempt to map between time bases, though that might be an approach for future improvements.

        @param previous_time - The previous timestamp to get the elapsed time from

        @return The elapsed time in seconds. Returns `None` if one or both of the timestamps had no valid data for the
                all time bases.
        '''
        elapsed = None

        def _update_max_elapsed(a: Optional[float], b: Optional[float],
                                cur_max_elapsed: Optional[float]) -> Optional[float]:
            if a is not None and b is not None:
                new_elapsed = a - b
                if cur_max_elapsed is None or new_elapsed > cur_max_elapsed:
                    return new_elapsed

            return cur_max_elapsed

        elapsed = _update_max_elapsed(self.host_time, previous_time.host_time, elapsed)
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
    _metrics: ClassVar[Dict[str, 'MetricBase']] = {}
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
    # Controls if host time should be played back.
    _playback_host_times: ClassVar[bool] = False

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
    def playback_host_time(cls, log_dir: Path):
        file_path = log_dir / MSG_TIME_LOG_FILENAME
        if file_path.exists():
            cls._time_log_fd = open(file_path, 'rb')
            cls._playback_host_times = True
        else:
            logger.warning(f"{file_path} not found. Can't use host times for playback.")

    @classmethod
    def update_host_time(cls):
        '''!
        Update the global concept of current host time.

        This is used to set any checks that rely on a host TimeSource.
        This should only be called for realtime operation, and not called if playing back a log file.

        See the comment at the top of the file on how time keeping is performed.
        '''
        cls._current_time.host_time = time.monotonic()
        if cls._start_time.host_time is None:
            cls._start_time.host_time = cls._current_time.host_time

        # Check metrics triggered by elapsed host time.
        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._time_elapsed()

    @classmethod
    def update_device_time(cls, msg: MessageWithBytesTuple):
        '''!
        Update the global concept of current device time.

        This is used to set any checks that rely on a device TimeSource.
        This should be called for each message decoded from the device. This
        will update the current time with any time bases available in this
        message. Since most messages don't have timestamps for each time base,
        this means that one base may lag the other or that they may leapfrog.

        See the comment at the top of the file on how time keeping is performed.
        '''
        header, payload, _ = msg
        updated = False
        if cls._playback_host_times and cls._time_log_fd:
            data = cls._time_log_fd.read(8)
            if len(data) == 8:
                test_time_millis, seq_num = struct.unpack(_MSG_TIME_LOG_FORMAT, data)
                if seq_num != header.sequence_number:
                    logger.error(
                        f"Playback host times sequence number didn't match expected"
                        f" [time_seq_num={seq_num}, msg_seq_num={header.sequence_number}].")
                    # TODO: Try to resync
                    cls._time_log_fd = None
                else:
                    cls._current_time.host_time = test_time_millis / 1000.0
                    if cls._start_time.host_time is None:
                        cls._start_time.host_time = cls._current_time.host_time
                    updated = True
            else:
                cls._time_log_fd = None
                logger.error('Playback host times ran out of data.')

        if isinstance(payload, MessagePayload):
            p1_time = payload.get_p1_time()
            # Note p1_time bool check validates if p1_time not None and not NaN.
            if p1_time:
                updated = True
                cls._current_time.p1_time = p1_time.seconds
                if cls._start_time.p1_time is None:
                    cls._start_time.p1_time = p1_time.seconds
            system_time = payload.get_system_time_sec()
            if system_time is not None and not math.isnan(system_time):
                updated = True
                cls._current_time.system_time = system_time
                if cls._start_time.system_time is None:
                    cls._start_time.system_time = system_time

        # Only log host times when update_host_time() is being called.
        elapsed = cls._current_time.get_elapsed(cls._start_time, TimeSource.HOST)
        if elapsed is not None and cls._log_dir is not None and cls._log_msg_times and not cls._playback_host_times:
            if cls._time_log_fd is None:
                file_path = cls._log_dir / MSG_TIME_LOG_FILENAME
                cls._time_log_fd = open(file_path, 'wb')

            test_time_millis = round(elapsed * 1000.0)
            cls._time_log_fd.write(struct.pack(_MSG_TIME_LOG_FORMAT, test_time_millis, header.sequence_number))

        # Check metrics triggered by elapsed device time.
        if updated:
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

        Afterwards initialize each active metric.

        This is meant to run once on test initialization.
        '''
        for callback in cls._config_customization_callbacks:
            callback(env_args)

        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._initialize()

    @classmethod
    def finalize(cls):
        '''!
        Call metric `_finalize()` functions for processing metrics that are only checked on test completion.
        '''
        if cls._time_log_fd is not None:
            cls._time_log_fd.close()
        for metric in cls._metrics.values():
            if not metric.is_disabled:
                metric._finalize()
                if metric._log_fd:
                    metric._log_fd.close()
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
        has_failures = False
        for name, metric in cls._metrics.items():
            if not metric.is_disabled:
                results[name] = asdict(metric)
                has_failures |= metric.failure_time is not None

        return {'results': results, 'test_start': cls._start_time, 'has_failures': has_failures}


@dataclass
class MetricBase:
    '''!
    Base class for specifying test metrics.

    Metrics are dataclasses to simplify their declaration, initialization, and serialization.
    '''
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
        '''!
        Since `__init__` is handled by the dataclass code, __post_init__ is used to validate parameters and initialize
        internal values.
        '''
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

        # Declare here so it's not included as a field for serialization.
        self._log_fd: Optional[BinaryIO] = None

    def __log(self, value: float):
        if not self.is_disabled and MetricController._log_metric_values and MetricController._log_dir:
            if self._log_fd is None:
                file_path = MetricController._log_dir / (self.name + '.bin')
                self._log_fd = open(file_path, 'wb')
            elapsed = MetricController._current_time.get_max_elapsed(MetricController._start_time)
            test_time_millis = 0 if elapsed is None else round(elapsed * 1000.0)
            self._log_fd.write(struct.pack(_METRIC_LOG_FORMAT, test_time_millis, value))

    def _update_status(self, value: float, is_failure: bool, context: Optional[str] = None):
        '''!
        Log the value and the failure state as needed.

        When `is_failure==True` the failure_context will be set to `str(value)`.

        @param value Value to log.
        @param is_failure `True` if this value fail the metric's requirements.
        @param context Optional string to associate with the metric's failure.
        '''
        if self.is_disabled:
            return

        if not self.not_logged:
            self.__log(value)

        if context is None:
            context = str(value)

        self._update_failure(is_failure, context)

    def _update_failure(self, is_failure: bool, context: Optional[str] = None):
        '''!
        Update the metric's failure status.

        Failure timestamps and contexts are only set on the metric's first failure.

        @param is_failure `True` if metric's requirements have been violated.
        @param context Optional string to associate with the metric's failure.
        '''
        self.was_checked = True
        if self.is_disabled:
            return
        if is_failure and self.failure_time is None:
            logger.info(f'Failure {self.name}: {context}')
            self.failure_time = MetricController._current_time
            self.failure_context = context
            if self.is_fatal:
                raise FatalMetricException(self.name)

    def _initialize(self):
        '''!
        Callback to initialize metric after its configuration has been finalized.
        '''
        pass

    def _finalize(self):
        '''!
        Callback to run checks performed at the end of the test collection.

        For example, checks that require computing the stats for a value.
        '''
        pass

    def _time_elapsed(self):
        '''!
        Callback to run checks based on time elapsing.

        For example, checks on the elapsed time between events.
        '''
        pass


@dataclass
class MaxValueMetric(MetricBase):
    '''!
    Checks that a value never exceeds a specified threshold.
    '''
    threshold: float

    def check(self, value: float):
        self._update_status(value, value > self.threshold)


@dataclass
class MaxElapsedTimeMetric(MetricBase):
    '''!
    Validates that the elapsed time between checks never exceeds a time threshold.

    @note This metric is validated during MetricController.update_host_time() and MetricController.update_device_time()
          calls.
    '''
    # Time sources to compare for determining the elapsed time.
    time_source: TimeSource
    # Metric will fail if `check()` is not called before this many seconds into the test.
    max_time_to_first_check_sec: Optional[float] = None
    # Metric will fail if more than this many seconds elapses between between `check()` calls.
    max_time_between_checks_sec: Optional[float] = None

    # Timestamp when this metric was last checked. `None` if never checked.
    __last_time = None

    def check(self) -> Optional[float]:
        elapsed = None
        if isinstance(self.__last_time, Timestamp):
            elapsed = MetricController._current_time.get_elapsed(self.__last_time, self.time_source)
            if elapsed is None:
                return None
            # Failures are updated in _time_elapsed() function.
            self._update_status(elapsed, False)
        self.__last_time = deepcopy(MetricController._current_time)
        return elapsed

    def _time_elapsed(self):
        if isinstance(self.__last_time, Timestamp):
            if self.max_time_between_checks_sec is not None:
                elapsed = MetricController._current_time.get_elapsed(self.__last_time, self.time_source)
                if elapsed is None:
                    return
                self._update_failure(elapsed > self.max_time_between_checks_sec,
                                     f'time between checks: {elapsed} > {self.max_time_between_checks_sec}')
        else:
            if self.max_time_to_first_check_sec is not None:
                elapsed = MetricController._current_time.get_elapsed(MetricController._start_time, self.time_source)
                if elapsed is None:
                    return
                self._update_failure(elapsed > self.max_time_to_first_check_sec,
                                     f'time to first check: {elapsed} > {self.max_time_to_first_check_sec}')


class CdfThreshold(NamedTuple):
    '''!
    Struct for specifying a threshold for a statistical distribution check.
    '''
    # The percentile to check.
    percentile: float
    # The value to check at this percentile.
    threshold: float


@dataclass
class StatsMetric(MetricBase):
    '''!
    Any combination of the thresholds may be set. The check will fail if any of
    the thresholds are violated.

    max and min are validated by each `check()` call. CDF checks are only performed during test finalization.
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
    # Only check CDF thresholds if at least this many values have been computed.
    min_values_for_cdf_check: int = 100

    # For each threshold count to number of times the value was below the threshold.
    __below_threshold_counts = {}
    # Count of total number of times this metric was checked.
    __total_times_checked = 0

    def _initialize(self):
        if self.min_values_for_cdf_check < 1:
            raise ValueError(
                f"min_values_for_cdf_check for metric {self.name} can't be {self.min_values_for_cdf_check} which is < 1.")
        thresholds = set(k.threshold for k in self.max_cdf_thresholds)
        thresholds.update(k.threshold for k in self.min_cdf_thresholds)
        for threshold in thresholds:
            self.__below_threshold_counts[threshold] = 0

    def check(self, value: float):
        if not self.is_disabled:
            context = None
            if self.max_threshold is not None and value > self.max_threshold:
                context = f'{value} > max threshold ({self.max_threshold})'
            elif self.min_threshold is not None and value < self.min_threshold:
                context = f'{value} < min threshold ({self.min_threshold})'

            failed = context is not None

            self._update_status(value, failed, context)

            for k in self.__below_threshold_counts:
                if value < k:
                    self.__below_threshold_counts[k] += 1

            self.__total_times_checked += 1

    def _finalize(self):
        if not self.is_disabled and len(self.__below_threshold_counts) > 0 and \
                self.__total_times_checked >= self.min_values_for_cdf_check:
            failures = []

            empirical_percentiles = {}
            for threshold, counts in self.__below_threshold_counts.items():
                smaller_count = counts
                if self.__total_times_checked > self.min_values_for_cdf_check:
                    empirical_percentiles[threshold] = smaller_count / self.__total_times_checked * 100.0

            for percentile, threshold in self.max_cdf_thresholds:
                if threshold in empirical_percentiles:
                    if empirical_percentiles[threshold] < percentile:
                        failures.append(f'{percentile}th percentile ({empirical_percentiles[threshold]}) > {threshold}')

            for percentile, threshold in self.min_cdf_thresholds:
                if threshold in empirical_percentiles:
                    if empirical_percentiles[threshold] > percentile:
                        failures.append(
                            f'{percentile}th percentile ({empirical_percentiles[threshold] }) < {threshold}')

            if len(failures) > 0:
                self._update_failure(True, ','.join(failures))


@dataclass
class PercentTrueMetric(MetricBase):
    # Fail if less than this percent of checks are true.
    min_percent_true: float
    # Only check threshold if at least this many values have been computed.
    min_values_for_check: int = 100

    # Count of number of times value was `True`.
    __true_count = 0
    # Count of total number of times this metric was checked.
    __total_times_checked = 0

    def _initialize(self):
        if self.min_values_for_check < 1:
            raise ValueError(
                f"min_values_for_check for metric {self.name} can't be {self.min_values_for_check} which is < 1.")

    def check(self, value: bool):
        if not self.is_disabled:
            if value:
                self.__true_count += 1
            self.__total_times_checked += 1

    def _finalize(self):
        if not self.is_disabled and self.__total_times_checked >= self.min_values_for_check:
            measured_percent = self.__true_count / self.__total_times_checked * 100.0
            self._update_status(measured_percent, measured_percent < self.min_percent_true)


@dataclass
class MinValueMetric(MetricBase):
    '''!
    Checks that a value is never below a specified threshold.
    '''
    threshold: float

    def check(self, value: float):
        self._update_status(value, value < self.threshold)


@dataclass
class EqualValueMetric(MetricBase):
    '''!
    Checks that a value always matches a specified value.
    '''
    threshold: float
    '''!
    The maximum allowed difference between a and b, relative to the larger absolute value of a or b. For example, to set
    a tolerance of 5%, pass rel_tol=0.05. The default tolerance is 1e-09, which assures that the two values are the same
    within about 9 decimal digits. rel_tol must be greater than zero. See:
    https://docs.python.org/3/library/math.html#math.isclose
    '''
    rel_tol: float = 1e-09
    '''! The minimum absolute tolerance, useful for comparisons near zero. abs_tol must be at least zero. See:
    https://docs.python.org/3/library/math.html#math.isclose
    '''
    abs_tol: float = 0.0

    def check(self, value: float):
        self._update_status(value, not math.isclose(self.threshold, value, rel_tol=self.rel_tol, abs_tol=self.abs_tol))


@dataclass
class AlwaysTrueMetric(MetricBase):
    '''!
    Checks that a value is always `True`.
    '''

    def check(self, value: bool, failure_context: Optional[str] = None):
        self._update_status(value, not value, failure_context)


def _main():
    test1 = MaxValueMetric('test1', 'test1 description', 10)
    test2 = MaxValueMetric('test2', 'test2 description', 10)
    test3 = StatsMetric(
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
