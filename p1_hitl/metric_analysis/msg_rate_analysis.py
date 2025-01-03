import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import NamedTuple, Optional

from fusion_engine_client.messages import (MessageRate, MessageType,
                                           NmeaMessageType)
from fusion_engine_client.parsers import FusionEngineDecoder
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

from p1_hitl.defs import DeviceType, HitlEnvArgs
from p1_hitl.metric_analysis.metrics import (AlwaysTrueMetric,
                                             EqualValueMetric,
                                             MetricController)
from p1_runner.nmea_framer import NMEAFramer
from p1_test_automation.devices_config import (DataSource, DeviceConfig,
                                               open_data_source)

from .base_analysis import AnalyzerBase

logger = logging.getLogger('point_one.hitl.analysis.msg-rates')

# To allow sufficient data for accurate rate checking, the first check is delayed.
_TIME_BEFORE_FIRST_CHECK_SEC = 242.0


class ExpectedMessageRate(NamedTuple):
    message_id: MessageType | NmeaMessageType
    rate: MessageRate


class DataChannelDefaultsCheck(NamedTuple):
    name: str
    # Currently only checking to confirm expected messages. No test for checking that unexpected messages aren't sent.
    expected_msg_rates: set[ExpectedMessageRate]
    # If `None` use the parsed data from the diagnostic channel.
    device_config: Optional[DeviceConfig] = None


def get_nmea_type(msg) -> NmeaMessageType:
    type_str = msg.split(',')[0]
    for t in NmeaMessageType:
        if t.name in type_str:  # type: ignore
            return t  # type: ignore

    return NmeaMessageType.INVALID


NMEA_GNSS_ONLY_DEFAULT_RATES = {
    ExpectedMessageRate(NmeaMessageType.GGA, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(NmeaMessageType.GLL, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(NmeaMessageType.GSA, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(NmeaMessageType.GSV, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(NmeaMessageType.RMC, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(NmeaMessageType.VTG, MessageRate.INTERVAL_100_MS),
}
NMEA_DEFAULT_RATES = NMEA_GNSS_ONLY_DEFAULT_RATES | {
    ExpectedMessageRate(
        NmeaMessageType.P1CALSTATUS,
        MessageRate.INTERVAL_10_S)}

FE_GNSS_ONLY_DEFAULT_RATES = {
    ExpectedMessageRate(MessageType.POSE, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(MessageType.POSE_AUX, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(MessageType.GNSS_INFO, MessageRate.INTERVAL_100_MS),
    ExpectedMessageRate(MessageType.GNSS_SATELLITE, MessageRate.INTERVAL_100_MS),
}
FE_DEFAULT_RATES = FE_GNSS_ONLY_DEFAULT_RATES | {
    ExpectedMessageRate(
        MessageType.CALIBRATION_STATUS,
        MessageRate.INTERVAL_10_S)}
FE_DEFAULT_RATES_WITH_IMU = FE_DEFAULT_RATES | {ExpectedMessageRate(MessageType.IMU_OUTPUT, MessageRate.ON_CHANGE)}


def get_device_defaults(env_args: HitlEnvArgs) -> list[DataChannelDefaultsCheck]:
    build_type = env_args.HITL_BUILD_TYPE
    if build_type.is_lg69t():
        nmea_rates = NMEA_GNSS_ONLY_DEFAULT_RATES if build_type.is_gnss_only() else NMEA_DEFAULT_RATES
        fe_rates = FE_GNSS_ONLY_DEFAULT_RATES if build_type.is_gnss_only() else FE_DEFAULT_RATES_WITH_IMU
        return [
            # UART1 (NMEA)
            DataChannelDefaultsCheck(
                name='uart1',
                device_config=DeviceConfig(name='uart1', serial_port=env_args.JENKINS_UART1),
                expected_msg_rates=nmea_rates
            ),
            # UART2 (diagnostic + FE + NMEA)
            DataChannelDefaultsCheck(
                name='uart2',
                device_config=None,
                # Since only FE is being passed from decoder in analysis_runner, can only check FE rates.
                expected_msg_rates=fe_rates
            )
        ]
    elif build_type in [DeviceType.AMAZON, DeviceType.ATLAS, DeviceType.BMW_MOTO]:
        return [
            # TCP1 (FE)
            DataChannelDefaultsCheck(
                name='tcp1',
                device_config=DeviceConfig(name='tcp1', tcp_address=env_args.JENKINS_LAN_IP, port=30200),
                expected_msg_rates=FE_DEFAULT_RATES
            ),
            # TCP2 (NMEA)
            DataChannelDefaultsCheck(
                name='tcp2',
                device_config=DeviceConfig(name='tcp2', tcp_address=env_args.JENKINS_LAN_IP, port=30201),
                # Since only FE is being passed from decoder in analysis_runner, can only check FE rates.
                expected_msg_rates=NMEA_DEFAULT_RATES
            )
        ]
    elif build_type is DeviceType.ZIPLINE:
        return [
            # TCP1 (FE)
            DataChannelDefaultsCheck(
                name='tcp1',
                device_config=DeviceConfig(name='tcp1', tcp_address=env_args.JENKINS_LAN_IP, port=30200),
                expected_msg_rates=FE_GNSS_ONLY_DEFAULT_RATES
            )
            # NMEA output disabled.
        ]
    else:
        raise RuntimeError(f'Default messages not known for specified device type ({build_type.name}).')


@dataclass
class CheckerInterfaceState:
    data_source: Optional[DataSource]
    nmea_framer: Optional[NMEAFramer]
    fe_decoder: Optional[FusionEngineDecoder]
    nmea_counts: dict[NmeaMessageType, int] = field(default_factory=lambda: defaultdict(int))
    fe_counts: dict[MessageType, int] = field(default_factory=lambda: defaultdict(int))
    last_nmea_type: NmeaMessageType = NmeaMessageType.INVALID


class MessageRateChecker:
    def __init__(self, channels: list[DataChannelDefaultsCheck]):
        self.channels = channels
        self.channel_states: list[CheckerInterfaceState] = []
        for channel in channels:
            fe_decoder = None
            nmea_framer = None
            data_source = None
            if channel.device_config is not None:
                if any(isinstance(m.message_id, MessageType) for m in channel.expected_msg_rates):
                    fe_decoder = FusionEngineDecoder()

                if any(isinstance(m.message_id, NmeaMessageType) for m in channel.expected_msg_rates):
                    nmea_framer = NMEAFramer()

                data_source = open_data_source(channel.device_config)

            self.channel_states.append(CheckerInterfaceState(data_source, nmea_framer, fe_decoder))

    def stop(self):
        for channel_state in self.channel_states:
            if channel_state.data_source is not None:
                channel_state.data_source.stop()

    def new_diag_message(self, diag_fe_message_type: MessageType):
        for channel_state in self.channel_states:
            # Check the data rate for the diagnostic interface used for HITL analysis.
            if channel_state.data_source is None:
                channel_state.fe_counts[diag_fe_message_type] += 1
            # Check the data rate on an alternative interface.
            else:
                data = channel_state.data_source.read(10240, 0)
                # Check NMEA message rates.
                if channel_state.nmea_framer is not None:
                    for msg in channel_state.nmea_framer.on_data(data):
                        nmea_type = get_nmea_type(msg)
                        # Since some messages (like $XXGSV) can be split or for multiple constellations, ignore
                        # back-to-back messages of the same type.
                        # This assumes at least two types of messages are sent in a burst.
                        if nmea_type != channel_state.last_nmea_type:
                            channel_state.nmea_counts[nmea_type] += 1
                            channel_state.last_nmea_type = nmea_type

                if channel_state.fe_decoder is not None:
                    for msg in channel_state.fe_decoder.on_data(data):
                        channel_state.fe_counts[MessageType(msg[0].message_type)] += 1


# Populated in `configure_metrics()`.
msg_rate_metrics: dict[str, EqualValueMetric] = {}
msg_on_metrics: dict[str, AlwaysTrueMetric] = {}


def get_metric_name(channel_name: str, type: IntEnum) -> str:
    return f'{channel_name}_{type.name.lower()}_rate'


def get_expected_period(rate: MessageRate) -> float:
    if rate == MessageRate.INTERVAL_10_MS:
        return 0.01
    elif rate == MessageRate.INTERVAL_20_MS:
        return 0.02
    elif rate == MessageRate.INTERVAL_40_MS:
        return 0.04
    elif rate == MessageRate.INTERVAL_50_MS:
        return 0.05
    elif rate == MessageRate.INTERVAL_100_MS:
        return 0.1
    elif rate == MessageRate.INTERVAL_200_MS:
        return 0.2
    elif rate == MessageRate.INTERVAL_500_MS:
        return 0.5
    elif rate == MessageRate.INTERVAL_1_S:
        return 1.0
    elif rate == MessageRate.INTERVAL_2_S:
        return 2.0
    elif rate == MessageRate.INTERVAL_5_S:
        return 5.0
    elif rate == MessageRate.INTERVAL_10_S:
        return 10.0
    elif rate == MessageRate.INTERVAL_30_S:
        return 30.0
    elif rate == MessageRate.INTERVAL_60_S:
        return 60.0
    else:
        raise ValueError(f'Unknown rate: {rate}')


def configure_metrics(env_args: HitlEnvArgs):
    params = env_args.get_selected_test_type().get_test_params()
    # Don't check rates if the device is being reset.
    if not params.has_resets:
        expected_rates = get_device_defaults(env_args)
        for channel in expected_rates:
            for rate in channel.expected_msg_rates:
                metric_name = get_metric_name(channel.name, rate.message_id)
                if rate.rate == MessageRate.ON_CHANGE:
                    msg_on_metrics[metric_name] = AlwaysTrueMetric(
                        metric_name,
                        f'Expect {rate.message_id.name} messages on {channel.name} channel.',
                    )
                    msg_on_metrics[metric_name]
                else:
                    msg_rate_metrics[metric_name] = EqualValueMetric(
                        metric_name,
                        f'Expected rate of {rate.message_id.name} messages on {channel.name} channel.',
                        threshold=get_expected_period(rate.rate),
                        # Allow 10% tolerance for the rate check.
                        rel_tol=0.1
                    )


MetricController.register_environment_config_customizations(configure_metrics)


class MessageRateAnalyzer(AnalyzerBase):
    '''!
    @brief Check the interface output rates for the expected messages.

    @note Since this class connects to additional interfaces, it is only valid for real-time analysis. This means that
        failures can not be validated.
    '''

    def __init__(self, env_args: HitlEnvArgs):
        super().__init__(env_args)
        self.checker = MessageRateChecker(get_device_defaults(env_args))
        self.start_time = time.monotonic()

    def update(self, msg: MessageWithBytesTuple):
        if self.params.has_resets:
            return

        header, _, _ = msg
        elapsed = time.monotonic() - self.start_time
        self.checker.new_diag_message(header.message_type)
        if elapsed > _TIME_BEFORE_FIRST_CHECK_SEC:
            for i, channel in enumerate(self.checker.channels):
                for rate in channel.expected_msg_rates:
                    metric_name = get_metric_name(channel.name, rate.message_id)
                    if isinstance(rate.message_id, MessageType):
                        count = self.checker.channel_states[i].fe_counts[rate.message_id]
                    else:
                        count = self.checker.channel_states[i].nmea_counts[rate.message_id]
                    empirical_rate = 0.0 if count == 0 else elapsed / float(count)

                    if rate.rate == MessageRate.ON_CHANGE:
                        msg_on_metrics[metric_name].check(count > 0, 'No messages received.')
                    else:
                        msg_rate_metrics[metric_name].check(empirical_rate)

    def stop(self):
        self.checker.stop()
