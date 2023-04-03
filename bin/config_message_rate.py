################################################################################
# @brief Message rate configuration helper functions.
#
# This file is meant to be used by `config_tool.py`.
################################################################################

from typing import Dict, List, Union

import fnmatch

from fusion_engine_client.messages import *

from p1_runner import trace as logging
from p1_runner.device_interface import DeviceInterface

logger = logging.getLogger('point_one.config_tool')

INTERFACE_MAP = {
    'current': InterfaceID(TransportType.CURRENT, 0),
    'uart1': InterfaceID(TransportType.SERIAL, 1),
    'uart2': InterfaceID(TransportType.SERIAL, 2)
}

PROTOCOL_MAP = {
    'fe': ProtocolType.FUSION_ENGINE,
    'fusionengine': ProtocolType.FUSION_ENGINE,
    'fusion_engine': ProtocolType.FUSION_ENGINE,
    'nmea': ProtocolType.NMEA,
    'rtcm': ProtocolType.RTCM,
    'all': ProtocolType.ALL,
}

MESSAGE_RATE_MAP = {
    'off': MessageRate.OFF,
    'on': MessageRate.ON_CHANGE,
    'on_change': MessageRate.ON_CHANGE,
    '60s': MessageRate.INTERVAL_60_S,
    '30s': MessageRate.INTERVAL_30_S,
    '10s': MessageRate.INTERVAL_10_S,
    '5s': MessageRate.INTERVAL_5_S,
    '2s': MessageRate.INTERVAL_2_S,
    '1s': MessageRate.INTERVAL_1_S,
    '500ms': MessageRate.INTERVAL_500_MS,
    '200ms': MessageRate.INTERVAL_200_MS,
    '100ms': MessageRate.INTERVAL_100_MS,
    'default': MessageRate.DEFAULT,
}


def _get_diagnostics_config_type(interface: InterfaceID):
    if interface.type == TransportType.SERIAL:
        if interface.index == 1:
            return Uart1DiagnosticMessagesEnabled
        elif interface.index == 2:
            return Uart2DiagnosticMessagesEnabled
    raise ValueError(f'Diagnostics config type not found for {str(interface)}.')


def _search_message_ids(name_to_message_id: Dict[str, int], query: str) -> List[int]:
    # Split comma-separated strings: 'pose, imu, gnss*' --> ['pose', 'imu', 'gnss*']
    message_id_strs = [s.strip() for s in query.split(',')]

    # Convert known message IDs to lowercase for case-insensitive search below.
    message_id_to_display_name = {v: k for k, v in name_to_message_id.items()}
    name_to_message_id = {k.lower(): v for k, v in name_to_message_id.items()}
    known_message_ids = set(int(v) for v in name_to_message_id.values())

    # Find messages matching each query.
    message_ids = set()
    for message_id_str in message_id_strs:
        # Try the message ID as an integer first.
        try:
            message_id = int(message_id_str)
            if message_id in known_message_ids:
                message_ids.add(message_id)
            else:
                raise ValueError('Unrecognized FusionEngine message ID %d.' % message_id)
        # If that fails, continue below to try to find the message by name or by partial-match.
        except ValueError:
            pass

        # Convert to lowercase for case-insensitive match.
        lower_name = message_id_str.lower()

        # If the string has a * in it, perform a wildcard search and allow multiple matches.
        if '*' in message_id_str:
            matches = set(int(v) for k, v in name_to_message_id.items() if fnmatch.fnmatch(k, lower_name))
            message_ids |= matches
            continue

        # If it's not a wildcard, try for an exact match.
        message_type = name_to_message_id.get(lower_name, None)
        if message_type is not None:
            message_ids.add(int(message_type))
            continue

        # Finally, if we can't find an exact match for the key, try a partial match. Unlike wildcard searches where the
        # user specifically wants multiple hits, partial matches do _not_ allow multiple hits.
        matches = {k: v for k, v in name_to_message_id.items() if k.startswith(lower_name)}

        # If there are multiple partial matches, also include names with "message" or "measurement" removed to
        # allow for exact match testing (e.g., match either "Pose" and "PoseMessage" (case-insensitive) for
        # PoseMessage, instead of hitting a multiple match error for "Pose" compared with both PoseMessage and
        # PoseAuxMessage)
        if len(matches) > 1:
            exact_matches = []
            for k in matches:
                if re.sub(r'(message|measurement)$', '', k) == lower_name:
                    exact_matches.append(k)

            if len(exact_matches) == 1:
                matches = {exact_matches[0]: matches[exact_matches[0]]}

        if len(matches) == 1:
            message_type = next(iter(matches.values()))
            message_ids.add(int(message_type))
        elif len(matches) > 1:
            types = [v for v in matches.values()]
            display_names = [message_id_to_display_name[t] for t in types]
            raise ValueError("Found multiple types matching '%s':\n  %s" %
                             (message_id_str, '\n  '.join(display_names))) from None
        else:
            raise ValueError("Unrecognized message type '%s'." % message_id_str) from None

    if len(message_ids) == 0:
        raise ValueError("No message types found matching '%s'." % query)
    else:
        return list(message_ids)


def _get_message_ids(protocol: ProtocolType, query: str) -> List[int]:
    if query.lower() == 'all' or query == '*':
        message_ids = [ALL_MESSAGES_ID]
    elif protocol == ProtocolType.NMEA:
        nmea_type_by_name = {str(t): t for t in NmeaMessageType}
        message_ids = _search_message_ids(nmea_type_by_name, query)
    elif protocol == ProtocolType.FUSION_ENGINE:
        message_ids = _search_message_ids(message_type_by_name, query)
    else:
        message_ids = [int(s.strip()) for s in query.split(',')]

    return message_ids


def parse_message_rate_args(interface: Union[InterfaceID, str], protocol: Union[ProtocolType, str],
                            message_id: Union[int, List[int], str]):
    if isinstance(interface, str):
        interface = INTERFACE_MAP[interface.lower()]

    if isinstance(protocol, str):
        protocol = PROTOCOL_MAP[protocol.lower()]

    if isinstance(message_id, str):
        msg_ids = _get_message_ids(protocol, message_id)
    elif isinstance(message_id, int):
        msg_ids = [message_id]
    else:
        msg_ids = message_id

    return [interface, protocol, msg_ids]


def message_rate_args_to_output_interface(cls, args, config_interface):
    # Special case, all protocols, all messages: apply uart1_message_rate 1s
    if args.rate is None and args.message_id is None:
        protocol = 'all'
        message_id = 'all'
        rate = args.protocol
    # Special case, specified protocol, all messages: apply uart1_message_rate fe 1s
    elif args.rate is None:
        protocol = args.protocol
        message_id = 'all'
        rate = args.message_id
    else:
        protocol = args.protocol
        message_id = args.message_id
        rate = args.rate

    (interface, protocol, message_ids) = parse_message_rate_args(interface=args.param.split('_')[0],
                                                                 protocol=protocol, message_id=message_id)

    try:
        rate = MESSAGE_RATE_MAP[rate.lower()]
    except KeyError:
        rate = MESSAGE_RATE_MAP[rate.lower().replace('_', '')]

    flags = 0
    if args.save:
        flags |= SetMessageRate.FLAG_APPLY_AND_SAVE
    if args.include_disabled:
        flags |= SetMessageRate.FLAG_INCLUDE_DISABLED_MESSAGES
    return [interface, protocol, message_ids, rate, flags]


def read_message_rate_config(config_interface: DeviceInterface,
                             interface: Union[InterfaceID, str],
                             protocol: Union[ProtocolType, str] = 'all',
                             message_id: Union[int, List[int], str] = 'all',
                             source: ConfigurationSource = ConfigurationSource.ACTIVE):
    interface_input = interface
    interface, protocol, message_ids = parse_message_rate_args(
        interface=interface, protocol=protocol, message_id=message_id)

    config_responses = []
    for message_id in message_ids:
        config_interface.get_message_rate(source, [interface, protocol, message_id])
        resp = config_interface.wait_for_message(MessageRateResponse.MESSAGE_TYPE)

        # Check if the response timed out.
        if resp is None:
            logger.error('Response timed out after %d seconds.' % config_interface.timeout)
            return None

        # Now print the response.
        if resp.response != Response.OK:
            logger.error('  %s: %s (%d)' % (str(interface), str(resp.response), int(resp.response)))
        else:
            for rate in resp.rates:
                modified_str = ''
                effective_str = ''
                if rate.protocol == ProtocolType.FUSION_ENGINE:
                    message_id_str = f'{MessageType(rate.message_id)} ({rate.message_id})'
                elif rate.protocol == ProtocolType.NMEA:
                    message_id_str = f'{NmeaMessageType(rate.message_id)} ({rate.message_id})'
                else:
                    message_id_str = f'{rate.message_id}'

                if isinstance(interface_input, str):
                    interface_str = f'{interface_input}_message_rate'
                else:
                    interface_str = f'{str(interface)}'
                label = f'{interface_str} {rate.protocol} {message_id_str}'

                if rate.configured_rate != rate.effective_rate:
                    effective_str = f' (effective rate is {rate.effective_rate} since diagnostics enabled)'
                if rate.flags & MessageRateResponse.FLAG_ACTIVE_DIFFERS_FROM_SAVED:
                    modified_str = ' (active differs from saved)'
                logger.info('  %s: %s%s%s', label, str(rate.configured_rate), effective_str, modified_str)

        config_responses.append(resp)
    return config_responses


def get_current_interface(config_interface: DeviceInterface) -> Optional[InterfaceID]:
    config_interface.get_message_rate(source=ConfigurationSource.ACTIVE, config_object=[
                                      INTERFACE_MAP['current'], ProtocolType.FUSION_ENGINE, MessageType.POSE])
    resp = config_interface.wait_for_message(MessageRateResponse.MESSAGE_TYPE)

    if isinstance(resp, MessageRateResponse):
        return resp.output_interface
    else:
        logger.error('Response timed out after %d seconds.' % config_interface.timeout)
        return None


def apply_message_rate_config(config_interface: DeviceInterface,
                              interface: Union[InterfaceID, str],
                              rate: MessageRate,
                              protocol: Union[ProtocolType, str] = 'all',
                              message_id: Union[int, List[int], str] = 'all',
                              flags: int = 0):
    interface, protocol, message_ids = parse_message_rate_args(interface=interface, protocol=protocol,
                                                               message_id=message_id)

    if len(message_ids) > 1:
        logger.info(f'Issuing rate change requests for {len(message_ids)} message types: '
                    f'{[m for m in message_ids]}')
    elif message_ids[0] == ALL_MESSAGES_ID:
        logger.info(f'Issuing rate change request for all message types.')
    else:
        logger.info(f'Issuing rate change request for 1 message type: '
                    f'{[m for m in message_ids]}')
    for message_id in message_ids:
        config_interface.set_message_rate([interface, protocol, message_id, rate, flags])
        resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Response timed out after %d seconds.' % config_interface.timeout)
            return False
        elif resp.response != Response.OK:
            logger.error('Apply command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
            return False

    return True


def copy_message_config(config_interface: DeviceInterface,
                        source: Union[InterfaceID, str],
                        dest: Union[InterfaceID, str],
                        message_rates: bool = True,
                        diagnostics_enabled: bool = True,
                        save: bool = False):
    if isinstance(source, str):
        source = INTERFACE_MAP[source.lower()]

    if isinstance(dest, str):
        dest = INTERFACE_MAP[dest.lower()]

    logger.debug(f'Copying message configuration from {str(source)} to {str(dest)}.')

    # Copy the message rates from the source device to the destination device.
    if message_rates:
        logger.debug(f'  Querying message rates.')
        config_interface.get_message_rate(source=ConfigurationSource.ACTIVE,
                                          config_object=[source, ProtocolType.ALL, ALL_MESSAGES_ID])
        read_resp = config_interface.wait_for_message(MessageRateResponse.MESSAGE_TYPE)
        if read_resp is None:
            logger.error('Timed out waiting for message rate query after %d seconds.' %
                         config_interface.timeout)
            return False
        elif read_resp.response != Response.OK:
            logger.error('Error querying message rates: %s (%d)' % (str(read_resp.response), int(read_resp.response)))
            return False

        logger.debug(f'  Setting message rates.')
        for rate in read_resp.rates:
            config_interface.set_message_rate([dest, rate.protocol, rate.message_id, rate.configured_rate, 0x0])
            resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
            if resp is None:
                logger.error('Timed out setting message rate after %d seconds.' % config_interface.timeout)
                return False
            elif resp.response != Response.OK:
                logger.error('Error setting message rate: %s (%d)' % (str(resp.response), int(resp.response)))
                return False

    # Query the source diagnostics enabled setting and apply it to the destination device.
    if diagnostics_enabled:
        try:
            source_diag_type = _get_diagnostics_config_type(source)
            dest_diag_type = _get_diagnostics_config_type(dest)
            logger.debug(f'  Querying diagnostics enabled state.')
            config_interface.get_config(source=ConfigurationSource.ACTIVE, config_type=source_diag_type.GetType())
            resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)
            if resp is None:
                logger.error('Timed out waiting for diagnostics config query after %d seconds.' %
                             config_interface.timeout)
                return False
            elif resp.response != Response.OK:
                logger.error('Error querying diagnostics state: %s (%d)' % (str(resp.response), int(resp.response)))
                return False

            logger.debug(f'  {"Enabling" if resp.config_object.value else "Disabling"} diagnostic output.')
            config_interface.set_config(dest_diag_type(resp.config_object.value))
            resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)
            if resp is None:
                logger.error('Timed out waiting for diagnostics set request after %d seconds.' %
                             config_interface.timeout)
                return False
            elif resp.response != Response.OK:
                logger.error('Error setting diagnostics state: %s (%d)' % (str(resp.response), int(resp.response)))
                return False
        except ValueError:
            # Diagnostics not supported for source and/or dest interface.
            pass

    # Save the config to disk.
    if save:
        logger.debug('  Saving settings.')
        config_interface.send_save()
        resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Timed out saving settings after %d seconds.' % config_interface.timeout)
            return False
        elif resp.response != Response.OK:
            logger.error('Save command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
            return False
        else:
            logger.info('Configuration saved successfully.')

    return True
