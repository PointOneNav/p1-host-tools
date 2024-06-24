#!/usr/bin/env python3

from datetime import datetime
import os
import socket
import sys

try:
    from websockets.sync.client import connect as websocket_connect
except ImportError:
    websocket_connect = None

import serial

from fusion_engine_client.messages import *
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR

# Add the parent directory to the search path to enable p1_runner and bin package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
sys.path.append(os.path.dirname(__file__))

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction, TriStateBooleanAction
from p1_runner.data_source import SerialDataSource, SocketDataSource, WebSocketDataSource
from p1_runner.device_interface import DeviceInterface, RESPONSE_TIMEOUT
from p1_runner.exported_data import (add_to_exported_data,
                                     create_exported_data,
                                     is_export_valid,
                                     load_saved_json,
                                     load_saved_data,
                                     user_config_from_platform_storage)
from p1_runner.find_serial_device import find_serial_device, PortType

from config_message_rate import *

logger = logging.getLogger('point_one.config_tool')


def _args_to_point3f(cls, args, config_interface):
    return cls(args.x, args.y, args.z)

def _args_to_heading_bias(cls, args, config_interface):
    return cls(args.horizontal, args.vertical)

SERIAL_TIMEOUT = 5


DEFAULT_TCP_PORT = 2332


_orientation_map = {
    'forward': Direction.FORWARD,
    'backward': Direction.BACKWARD,
    'left': Direction.LEFT,
    'right': Direction.RIGHT,
    'up': Direction.UP,
    'down': Direction.DOWN
}

_data_types_map = {
    'user_config': [DataType.USER_CONFIG],
    'filter_state': [DataType.FILTER_STATE],
    'calibration': [DataType.CALIBRATION_STATE],
    'all': [DataType.USER_CONFIG, DataType.FILTER_STATE, DataType.CALIBRATION_STATE],
}


def _str_to_direction(dir_str):
    return _orientation_map.get(dir_str, "")


_vehicle_model_map = {
    "unknown_vehicle": VehicleModel.UNKNOWN_VEHICLE,
    "dataspeed_cd4": VehicleModel.DATASPEED_CD4,
    "j1939": VehicleModel.J1939,
    "lexus_ct200h": VehicleModel.LEXUS_CT200H,
    "kia_sorento": VehicleModel.KIA_SORENTO,
    "kia_sportage": VehicleModel.KIA_SPORTAGE,
    "audi_q7": VehicleModel.AUDI_Q7,
    "audi_a8l": VehicleModel.AUDI_A8L,
    "tesla_model_x": VehicleModel.TESLA_MODEL_X,
    "tesla_model_3": VehicleModel.TESLA_MODEL_3,
    "hyundai_elantra": VehicleModel.HYUNDAI_ELANTRA,
    "peugeot_206": VehicleModel.PEUGEOT_206,
    "man_tgx": VehicleModel.MAN_TGX,
    "faction": VehicleModel.FACTION,
    "lincoln_mkz": VehicleModel.LINCOLN_MKZ,
    "bmw_7": VehicleModel.BMW_7
}


def _str_to_vehicle_model(vehicle_model_str):
    return _vehicle_model_map.get(vehicle_model_str, VehicleModel.UNKNOWN_VEHICLE)


_wheel_sensor_type_map = {
    "none": WheelSensorType.NONE,
    "ticks": WheelSensorType.TICKS,
    "wheel_speed": WheelSensorType.WHEEL_SPEED,
    "vehicle_speed": WheelSensorType.VEHICLE_SPEED,
    "vehicle_ticks": WheelSensorType.VEHICLE_TICKS
}


def _str_to_wheel_sensor_type(wheel_sensor_type_str):
    return _wheel_sensor_type_map.get(wheel_sensor_type_str, WheelSensorType.NONE)


_applied_speed_type_map = {
    "none": AppliedSpeedType.NONE,
    "rear_wheels": AppliedSpeedType.REAR_WHEELS,
    "front_wheels": AppliedSpeedType.FRONT_WHEELS,
    "front_and_rear_wheels": AppliedSpeedType.FRONT_AND_REAR_WHEELS,
    "vehicle_body": AppliedSpeedType.VEHICLE_BODY
}


def _str_to_applied_speed_type(applied_speed_type_str):
    return _applied_speed_type_map.get(applied_speed_type_str, AppliedSpeedType.NONE)


_steering_type_map = {
    "unknown": SteeringType.UNKNOWN,
    "front": SteeringType.FRONT,
    "front_and_rear": SteeringType.FRONT_AND_REAR
}


def _str_to_steering_type(steering_type_str):
    return _steering_type_map.get(steering_type_str, SteeringType.UNKNOWN)


_tick_mode_map = {
    "off": TickMode.OFF,
    "rising_edge": TickMode.RISING_EDGE,
    "falling_edge": TickMode.FALLING_EDGE
}


def _str_to_tick_mode(tick_mode_str):
    return _tick_mode_map.get(tick_mode_str, TickMode.OFF)


_tick_direction_map = {
    "off": TickDirection.OFF,
    "forward_active_high": TickDirection.FORWARD_ACTIVE_HIGH,
    "forward_active_low": TickDirection.FORWARD_ACTIVE_LOW
}


def _str_to_tick_direction(tick_direction_str):
    return _tick_direction_map.get(tick_direction_str, TickDirection.OFF)


_iono_delay_model_map = {
    "auto": IonoDelayModel.AUTO,
    "off": IonoDelayModel.OFF,
    "klobuchar": IonoDelayModel.KLOBUCHAR,
    "sbas": IonoDelayModel.SBAS
}


def _str_to_iono_delay_model(iono_delay_model_str):
    return _iono_delay_model_map.get(iono_delay_model_str, IonoDelayModel.AUTO)


_tropo_delay_model_map = {
    "auto": TropoDelayModel.AUTO,
    "off": TropoDelayModel.OFF,
    "saastamoinen": TropoDelayModel.SAASTAMOINEN
}


def _str_to_tropo_delay_model(tropo_delay_model_str):
    return _tropo_delay_model_map.get(tropo_delay_model_str, TropoDelayModel.AUTO)


def _args_to_coarse_orientation(cls, args, config_interface):
    return DeviceCourseOrientationConfig(_str_to_direction(args.x), _str_to_direction(args.z))


def _args_to_vehicle_details(cls, args, config_interface):
    # Query the existing parameters, so we can use those values if any of the user settings are unspecified.
    config_interface.get_config(ConfigurationSource.ACTIVE, VehicleDetailsConfig.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        raise RuntimeError('Response timed out after %d seconds while querying current values.' % RESPONSE_TIMEOUT)

    if args.vehicle_model is None:
        vehicle_model = resp.config_object.vehicle_model
    else:
        vehicle_model = _str_to_vehicle_model(args.vehicle_model)

    wheelbase_m = args.wheelbase if args.wheelbase is not None else resp.config_object.wheelbase_m
    front_track_width_m = (args.front_track_width if args.front_track_width is not None else
                           resp.config_object.front_track_width_m)
    rear_track_width_m = (args.rear_track_width if args.rear_track_width is not None else
                          resp.config_object.rear_track_width_m)

    return VehicleDetailsConfig(vehicle_model=vehicle_model, wheelbase_m=wheelbase_m,
                                front_track_width_m=front_track_width_m, rear_track_width_m=rear_track_width_m)


def _args_to_wheel_config(cls, args, config_interface):
    # Query the existing parameters, so we can use those values if any of the user settings are unspecified.
    config_interface.get_config(ConfigurationSource.ACTIVE, WheelConfig.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        raise RuntimeError('Response timed out after %d seconds while querying current values.' % RESPONSE_TIMEOUT)

    new_values = {}

    if args.wheel_sensor_type is not None:
        new_values['wheel_sensor_type'] = _str_to_wheel_sensor_type(args.wheel_sensor_type)

    if args.applied_speed_type is not None:
        new_values['applied_speed_type'] = _str_to_applied_speed_type(args.applied_speed_type)

    if args.steering_type is not None:
        new_values['steering_type'] = _str_to_steering_type(args.steering_type)

    if args.steering_ratio is not None:
        new_values['steering_ratio'] = args.steering_ratio

    if args.wheel_update_interval is not None:
        new_values['wheel_update_interval_sec'] = args.wheel_update_interval

    if args.wheel_tick_output_interval is not None:
        new_values['wheel_tick_output_interval_sec'] = args.wheel_tick_output_interval

    if args.meters_per_tick is not None:
        new_values['wheel_ticks_to_m'] = args.meters_per_tick

    if args.wheel_tick_max_value is not None:
        new_values['wheel_tick_max_value'] = args.wheel_tick_max_value

    if args.wheel_ticks_signed is not None:
        new_values['wheel_ticks_signed'] = args.wheel_ticks_signed

    if args.wheel_ticks_always_increase is not None:
        new_values['wheel_ticks_always_increase'] = args.wheel_ticks_always_increase

    result = resp.config_object._replace(**new_values)
    return result


def _args_to_hardware_tick_config(cls, args, config_interface):
    # Query the existing parameters, so we can use those values if any of the user settings are unspecified.
    config_interface.get_config(ConfigurationSource.ACTIVE, HardwareTickConfig.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        raise RuntimeError('Response timed out after %d seconds while querying current values.' % RESPONSE_TIMEOUT)

    if args.tick_mode is None:
        tick_mode = resp.config_object.tick_mode
    else:
        tick_mode = _str_to_tick_mode(args.tick_mode)

    if args.tick_direction is None:
        tick_direction = resp.config_object.tick_direction
    else:
        tick_direction = _str_to_tick_direction(args.tick_direction)

    if args.meters_per_tick is None:
        wheel_ticks_to_m = resp.config_object.wheel_ticks_to_m
    else:
        wheel_ticks_to_m = args.meters_per_tick

    return HardwareTickConfig(tick_mode=tick_mode, tick_direction=tick_direction, wheel_ticks_to_m=wheel_ticks_to_m)


def _args_to_enabled_gnss_systems(cls, args, config_interface):
    # Construct a bitmask based on the user settings.
    systems = [s.strip() for s in args.systems.split(',')]
    mask = SatelliteTypeMask.to_bit_mask(systems)

    # If the user requested 'only', enable the specified systems and disable all others by doing a set config with the
    # mask from above.
    if args.action == 'only':
        pass
    # Otherwise, read the config first so we can apply the user's changes.
    else:
        config_interface.get_config(ConfigurationSource.ACTIVE, ConfigType.ENABLED_GNSS_SYSTEMS)
        resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Read timed out after %d seconds.' % RESPONSE_TIMEOUT)
            return False
        elif resp.response != Response.OK:
            logger.error('Error querying GNSS systems: %s (%d)' % (str(resp.response), int(resp.response)))
            return False

        if args.action == 'on':
            mask |= resp.config_object.value
        else:
            mask = resp.config_object.value & (~mask)

    # Finally, return a config object.
    return EnabledGNSSSystemsConfig(mask)


def _args_to_enabled_gnss_frequencies(cls, args, config_interface):
    # Construct a bitmask based on the user settings.
    frequency_bands = [s.strip() for s in args.frequencies.split(',')]
    mask = FrequencyBandMask.to_bit_mask(frequency_bands)

    # If the user requested 'only', enable the specified bands and disable all others by doing a set config with the
    # mask from above.
    if args.action == 'only':
        pass
    # Otherwise, read the config first so we can apply the user's changes.
    else:
        config_interface.get_config(ConfigurationSource.ACTIVE, ConfigType.ENABLED_GNSS_FREQUENCY_BANDS)
        resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Read timed out after %d seconds.' % RESPONSE_TIMEOUT)
            return False
        elif resp.response != Response.OK:
            logger.error('Error querying GNSS frequency bands: %s (%d)' % (str(resp.response), int(resp.response)))
            return False

        if args.action == 'on':
            mask |= resp.config_object.value
        else:
            mask = resp.config_object.value & (~mask)

    # Finally, return a config object.
    return EnabledGNSSFrequencyBandsConfig(mask)


def _args_to_ionosphere_config(cls, args, config_interface):
    # Query the existing parameters, so we can use those values if any of the user settings are unspecified.
    config_interface.get_config(ConfigurationSource.ACTIVE, IonosphereConfig.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        raise RuntimeError('Response timed out after %d seconds while querying current values.' % RESPONSE_TIMEOUT)

    if args.iono_delay_model is None:
        iono_delay_model = resp.config_object.iono_delay_model
    else:
        iono_delay_model = _str_to_iono_delay_model(args.iono_delay_model)

    return IonosphereConfig(iono_delay_model=iono_delay_model)


def _args_to_troposphere_config(cls, args, config_interface):
    # Query the existing parameters, so we can use those values if any of the user settings are unspecified.
    config_interface.get_config(ConfigurationSource.ACTIVE, TroposphereConfig.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        raise RuntimeError('Response timed out after %d seconds while querying current values.' % RESPONSE_TIMEOUT)

    if args.tropo_delay_model is None:
        tropo_delay_model = resp.config_object.tropo_delay_model
    else:
        tropo_delay_model = _str_to_tropo_delay_model(args.tropo_delay_model)

    return TroposphereConfig(tropo_delay_model=tropo_delay_model)


def _args_to_int_gen(arg_name):
    def _func(cls, args, config_interface):
        return cls(int(getattr(args, arg_name)))
    return _func


def _args_to_bool(cls, args, config_interface):
    return cls(args.enabled)


def _args_to_id(cls, args, config_interface):
    if len(args.device_id) > 32:
        raise RuntimeError('User device ID must have length less than or equal to 32 characters.')
    return cls(args.device_id)


def _args_to_address(cls, args, config_interface):
    if len(args.remote_address) > 64:
        raise RuntimeError('Address must have length less than or equal to 64 characters.')
    return cls(args.remote_address)


def _args_to_float(cls, args, config_interface):
    return cls(float(args.x))


def _args_to_interface_config(cls, args, config_interface):
    return []


_transport_direction_map = {
    'server': TransportDirection.SERVER,
    'client': TransportDirection.CLIENT,
}


def _str_to_transport_direction(key):
    return _transport_direction_map.get(key, "")


_socket_type_map = {
    'stream': SocketType.STREAM,
    'datagram': SocketType.DATAGRAM,
    'sequence': SocketType.SEQPACKET,
}


def _str_to_socket_type(key):
    return _socket_type_map.get(key, "")


PARAM_DEFINITION = {
    'gnss': {'format': GnssLeverArmConfig, 'arg_parse': _args_to_point3f},
    'device': {'format': DeviceLeverArmConfig, 'arg_parse': _args_to_point3f},
    'orientation': {'format': DeviceCourseOrientationConfig, 'arg_parse': _args_to_coarse_orientation},
    'output': {'format': OutputLeverArmConfig, 'arg_parse': _args_to_point3f},
    'heading_bias': {'format': HeadingBias, 'arg_parse': _args_to_heading_bias},

    'gnss_systems':  {'format': EnabledGNSSSystemsConfig, 'arg_parse': _args_to_enabled_gnss_systems},
    'gnss_frequencies':  {'format': EnabledGNSSFrequencyBandsConfig, 'arg_parse': _args_to_enabled_gnss_frequencies},
    'leap_second': {'format': LeapSecondConfig, 'arg_parse': _args_to_int_gen('value')},
    'gps_week_rollover': {'format': GPSWeekRolloverConfig, 'arg_parse': _args_to_int_gen('value')},
    'ionosphere_config': {'format': IonosphereConfig, 'arg_parse': _args_to_ionosphere_config},
    'troposphere_config': {'format': TroposphereConfig, 'arg_parse': _args_to_troposphere_config},

    'vehicle_details': {'format': VehicleDetailsConfig, 'arg_parse': _args_to_vehicle_details},
    'wheel_config': {'format': WheelConfig, 'arg_parse': _args_to_wheel_config},
    'hardware_tick_config': {'format': HardwareTickConfig, 'arg_parse': _args_to_hardware_tick_config},

    'watchdog_enabled': {'format': WatchdogTimerEnabled, 'arg_parse': _args_to_bool},
    'user_device_id': {'format': UserDeviceID, 'arg_parse': _args_to_id},

    'uart1_baud': {'format': Uart1BaudConfig, 'arg_parse': _args_to_int_gen('baud_rate')},
    'uart2_baud': {'format': Uart2BaudConfig, 'arg_parse': _args_to_int_gen('baud_rate')},
    'uart1_diagnostics_enabled': {'format': Uart1DiagnosticMessagesEnabled, 'arg_parse': _args_to_bool},
    'uart2_diagnostics_enabled': {'format': Uart2DiagnosticMessagesEnabled, 'arg_parse': _args_to_bool},
    'current_message_rate': {'format': list, 'arg_parse': message_rate_args_to_output_interface},
    'uart1_message_rate': {'format': list, 'arg_parse': message_rate_args_to_output_interface},
    'uart2_message_rate': {'format': list, 'arg_parse': message_rate_args_to_output_interface},
}

INTERFACE_PARAM_DEFINITION = {
    'baud_rate': {'format': InterfaceBaudRateConfig, 'arg_parse': _args_to_int_gen('baud_rate')},
    'port': {'format': InterfacePortConfig, 'arg_parse': _args_to_int_gen('port')},
    'remote_address': {'format': InterfaceRemoteAddressConfig, 'arg_parse': _args_to_address},
    'enabled': {'format': InterfaceEnabledConfig, 'arg_parse': _args_to_bool},
    'direction': {'format': InterfaceDirectionConfig, 'arg_parse': _str_to_transport_direction},
    'socket_type': {'format': InterfaceDirectionConfig, 'arg_parse': _str_to_socket_type},
    'diagnostics_enabled': {'format': InterfaceDiagnosticMessagesEnabled, 'arg_parse': _args_to_bool},
    'message_rate': {'format': list, 'arg_parse': message_rate_args_to_output_interface},
}

_cocom_type = {str(e).lower(): e for e in CoComType}


def read_config(config_interface: DeviceInterface, args):
    if args.type == 'saved':
        source = ConfigurationSource.SAVED
        desc = "Saved"
    else:
        source = ConfigurationSource.ACTIVE
        desc = "Active"

    logger.debug('Reading %s configuration.' % desc.lower())

    # If the user did not specify a parameter to read, read all configuration parameters.
    read_all = args.param is None
    interface = None
    if read_all:
        params = PARAM_DEFINITION.copy()
        del params['current_message_rate']
    # If the config targets an interface read its parameters.
    elif 'interface_config_type' in args:
        params = {args.interface_config_type: INTERFACE_PARAM_DEFINITION[args.interface_config_type]}
        interface = args.param
    # Otherwise, read a single parameter.
    else:
        params = {args.param: PARAM_DEFINITION[args.param]}

    config_responses = []

    # For each listed parameter, issue a GetConfig request to the device and wait for the response.
    logger.info('%s parameter values:' % desc)
    for key, definition in params.items():
        # Skip this parameter if read is disabled explicitly in its definition.
        skip = definition.get('skip_read')
        if skip:
            logger.info('  %s: No read defined', key)
            continue

        # If this is a message_rate query, we handle it differently. Message rate queries are done via
        # GetMessageRate requests, specifying the interface, protocol, and message ID. They do not use GetConfig.
        if key.endswith('message_rate'):
            # If this is a uartN_message_rate request, get the interface key.
            if interface is None:
                selected_interface = key.split('_')[0]
            else:
                selected_interface = interface

            ret = read_message_rate_config(config_interface=config_interface, source=source,
                                           interface=selected_interface, protocol=getattr(args, 'protocol', 'all'),
                                           message_id=getattr(args, 'message_id', 'all'))
            if not ret:
                return None
            else:
                config_responses += ret
        # Otherwise, issue a GetConfig for the payload type corresponding with this parameter.
        else:
            format = definition['format']
            if interface:
                subtype = format.GetSubtype()
                interface_id = INTERFACE_MAP[interface]
                config_interface.get_config(source, InterfaceConfigSubmessage(interface_id, subtype))
            else:
                type = format.GetType()
                config_interface.get_config(source, type)
            resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

            # Check if the response timed out.
            if resp is None:
                logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
                return None

            # Now print the response.
            interface_str = f'{interface} ' if interface else ''
            if resp.response != Response.OK:
                logger.error('  %s%s: %s (%d)' % (interface_str, key, str(resp.response), int(resp.response)))
            else:
                modified_str = ''
                if resp.flags & ConfigResponseMessage.FLAG_ACTIVE_DIFFERS_FROM_SAVED:
                    modified_str = '(active differs from saved)'
                logger.info('  %s%s: %s %s', interface_str, key, str(resp.config_object), modified_str)

            config_responses.append(resp)

    return config_responses


def revert_config(config_interface: DeviceInterface, args):
    logger.debug('Revert configuration.')

    # If the user did not specify a parameter to read, revert all configuration parameters.
    revert_all = args.param is None
    interface = None
    if revert_all:
        params = PARAM_DEFINITION.copy()
        del params['current_message_rate']
    # If the config targets an interface read its parameters.
    elif 'interface_config_type' in args:
        params = {args.interface_config_type: INTERFACE_PARAM_DEFINITION[args.interface_config_type]}
        interface = args.param
    # Otherwise, read a single parameter.
    else:
        params = {args.param: PARAM_DEFINITION[args.param]}

    # For each listed parameter, issue a SetConfig request to the device and wait for the response.
    for key, definition in params.items():
        # If this is a message_rate query, we handle it differently. Message rate changes are done via
        # SetMessageRate requests, specifying the interface, protocol, and message ID. They do not use SetConfig.
        if key.endswith('message_rate'):
            # If this is a uartN_message_rate request, get the interface key.
            if interface is None:
                selected_interface = key.split('_')[0]
            else:
                selected_interface = interface

            flags = 0
            if args.save:
                flags |= SetMessageRate.FLAG_APPLY_AND_SAVE
            if args.include_disabled:
                flags |= SetMessageRate.FLAG_INCLUDE_DISABLED_MESSAGES

            ret = apply_message_rate_config(
                config_interface=config_interface, interface=selected_interface,
                rate=MessageRate.DEFAULT, protocol=getattr(args, 'protocol', 'all'),
                message_id=getattr(args, 'message_id', 'all'), flags=flags)
            if not ret:
                return False
        # Otherwise, issue a GetConfig for the payload type corresponding with this parameter.
        else:
            format = definition['format']
            interface_id = INTERFACE_MAP[interface] if interface else None
            config_interface.set_config(format(), args.save, revert=True, interface=interface_id)

            resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)

            # Check if the response timed out.
            if resp is None:
                logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
                return None

            # Now print the response.
            interface_str = f'{interface} ' if interface else ''
            if resp.response != Response.OK:
                logger.error('  %s%s: %s (%d)' % (interface_str, key, str(resp.response), int(resp.response)))
                return False

        logger.info('  %s: reverted', key)

    return True


def apply_config(config_interface: DeviceInterface, args):
    # If the config targets an interface read its parameters.
    interface = None
    if 'interface_config_type' in args:
        definition = INTERFACE_PARAM_DEFINITION[args.interface_config_type]
        interface = args.param
        param = args.interface_config_type
    else:
        definition = PARAM_DEFINITION[args.param]
        param = args.param

    format = definition['format']
    arg_parse = definition['arg_parse']
    config_object = arg_parse(cls=format, args=args, config_interface=config_interface)

    logger.debug('Applying config parameter update.')
    if param.endswith('message_rate'):
        interface, protocol, message_ids, rate, flags = config_object
        if not apply_message_rate_config(config_interface=config_interface,
                                         interface=interface, protocol=protocol, message_id=message_ids,
                                         rate=rate, flags=flags):
            return False
    else:
        interface_id = INTERFACE_MAP[interface] if interface else None
        config_interface.set_config(config_object, save=args.save, interface=interface_id)
        resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
            return False
        elif resp.response != Response.OK:
            logger.error('Apply command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
            return False

    logger.info('Parameter changed successfully.')
    return True


def save_config(config_interface: DeviceInterface, args):
    logger.debug('Saving lever arm parameter updates.')
    if args.revert_to_saved:
        action = SaveAction.REVERT_TO_SAVED
    elif args.revert_to_defaults:
        action = SaveAction.REVERT_TO_DEFAULT
    else:
        action = SaveAction.SAVE

    config_interface.send_save(action)

    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return False
    elif resp.response != Response.OK:
        logger.error('Saving command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
        return False
    else:
        logger.info('Configuration saved successfully.')
        return True


def copy_interface_message_config(config_interface: DeviceInterface, args):
    logger.debug(f'Copying message settings from {args.source} to {args.dest}.')
    if copy_message_config(config_interface=config_interface, source=args.source, dest=args.dest,
                           message_rates=args.message_rates, diagnostics_enabled=args.diagnostics_enabled,
                           save=args.save):
        logger.info('Configuration copied successfully.')
        return True
    else:
        # copy_message_config() will print an error.
        return False

def query_system_status(config_interface: DeviceInterface, args):
    logger.debug('Querying system status info.')
    config_interface.send_message(MessageRequest(MessageType.SYSTEM_STATUS))

    resp = config_interface.wait_for_message(MessageType.SYSTEM_STATUS)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
    else:
        logger.info(str(resp))
    return resp

def query_device_id(config_interface: DeviceInterface, args):
    logger.debug('Querying Device ID info.')
    config_interface.send_message(MessageRequest(MessageType.DEVICE_ID))

    resp = config_interface.wait_for_message(MessageType.DEVICE_ID)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
    else:
        logger.info(str(resp))
    return resp

def query_version(config_interface: DeviceInterface, args):
    if args.type == 'nmea':
        return query_nmea_versions(config_interface, args)
    else:
        return query_fe_version(config_interface, args)


def query_fe_version(config_interface: DeviceInterface, args):
    logger.debug('Querying version info.')
    config_interface.send_message(MessageRequest(MessageType.VERSION_INFO))

    resp = config_interface.wait_for_message(VersionInfoMessage.MESSAGE_TYPE)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
    else:
        logger.info(str(resp))
    return resp


def query_nmea_versions(config_interface: DeviceInterface, args):
    logger.debug('Querying NMEA version info.')
    versions = []

    config_interface.send_message('$PQTMVERNO')
    resp = config_interface.wait_for_message('$PQTMVERNO')
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return None
    else:
        logger.info(resp)
        versions.append(resp)

    config_interface.send_message('$PQTMVERNO,SUB')
    resp = config_interface.wait_for_message('$PQTMVERNO,SUB')
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return None
    else:
        logger.info(resp)
        versions.append(resp)
    return versions


def query_interfaces(config_interface: DeviceInterface, args):
    logger.debug('Querying device interface list.')
    config_interface.send_message(MessageRequest(MessageType.SUPPORTED_IO_INTERFACES))

    resp = config_interface.wait_for_message(MessageType.SUPPORTED_IO_INTERFACES)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
    else:
        logger.info(str(resp))
    return resp


def request_reset(config_interface: DeviceInterface, args):
    mask = 0
    for reset_type in args.type:
        if reset_type == 'factory':
            logger.info('Issuing factory reset request.')
            mask |= ResetRequest.FACTORY_RESET
        elif reset_type == 'hot':
            logger.info('Issuing hot start request.')
            mask |= ResetRequest.HOT_START
        elif reset_type == 'warm':
            logger.info('Issuing warm start request.')
            mask |= ResetRequest.WARM_START
        elif reset_type == 'pvt':
            logger.info('Issuing a PVT reset request.')
            mask |= ResetRequest.PVT_RESET
        elif reset_type == 'diag':
            logger.info('Issuing a diagnostic logging reset request.')
            mask |= ResetRequest.DIAGNOSTIC_LOG_RESET
        elif reset_type == 'cold':
            logger.info('Issuing cold start request.')
            mask |= ResetRequest.COLD_START
        elif reset_type == 'reboot':
            logger.info('Issuing reboot request.')
            mask |= ResetRequest.REBOOT_NAVIGATION_PROCESSOR
        elif reset_type == 'reboot_gnss':
            logger.info('Issuing GNSS receiver reboot request.')
            mask |= ResetRequest.REBOOT_GNSS_MEASUREMENT_ENGINE
        elif reset_type == 'calibration':
            logger.info('Issuing calibration reset request.')
            mask |= (ResetRequest.RESET_CALIBRATION_DATA |
                     ResetRequest.RESET_NAVIGATION_ENGINE_DATA)
        elif reset_type == 'config':
            logger.info('Issuing user configuration reset request. This may reset the device calibration.')
            mask |= ResetRequest.RESET_CONFIG
        elif reset_type == 'nav_engine':
            logger.info('Issuing navigation engine state reset request.')
            mask |= ResetRequest.RESET_NAVIGATION_ENGINE_DATA
        elif reset_type == 'position':
            logger.info('Issuing position reset request.')
            mask |= ResetRequest.RESET_POSITION_DATA
        else:
            logger.error("Unrecognized reset type '%s'." % reset_type)
            return False

    config_interface.send_message(ResetRequest(mask))

    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return False
    elif resp.response != Response.OK:
        logger.error('Reset command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
        return False
    else:
        logger.info('Reset successful.')
        return True


def request_shutdown(config_interface: DeviceInterface, args):
    flags = {
        'engine': ShutdownRequest.STOP_ENGINE,
        'log': ShutdownRequest.STOP_CURRENT_LOG,
    }[args.type]
    config_interface.send_message(ShutdownRequest(flags))

    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return False
    elif resp.response != Response.OK:
        logger.error('Shutdown command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
        return False
    else:
        logger.info('Shutdown successful.')
        return True


def request_startup(config_interface: DeviceInterface, args):
    flags = {
        'engine': StartupRequest.START_ENGINE,
        'log': StartupRequest.START_NEW_LOG,
    }[args.type]
    config_interface.send_message(StartupRequest(flags))

    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
    if resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return False
    elif resp.response != Response.OK:
        logger.error('Startup command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
        return False
    else:
        logger.info('Startup successful.')
        return True


def request_export(config_interface: DeviceInterface, args):
    data_types = _data_types_map[args.type]
    responses = []

    if args.format == 'json':
        if args.type == 'all':
            logger.info('JSON export only valid for user_config.')
            data_types = [DataType.USER_CONFIG]
        elif args.type != 'user_config':
            logger.error('JSON export only valid for user_config. Use `--type=user_config`.')
            return None

    # Query device for version to save with metadata.
    config_interface.send_message(MessageRequest(MessageType.VERSION_INFO))
    version_resp = config_interface.wait_for_message(VersionInfoMessage.MESSAGE_TYPE)
    if version_resp is None:
        logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
        return None
    assert isinstance(version_resp, VersionInfoMessage)

    export_file = args.export_file
    if export_file is None:
        timestr = datetime.now().strftime("%y%m%d_%H%M%S")
        device_type = '-'.join(version_resp.engine_version_str.split('-')[:2])
        if args.format == 'json':
            export_file = device_type + '.user_config.' + timestr + '.json'
        else:
            export_file = device_type + '.' + timestr + '.p1nvm'
    logger.info('Exporting to %s', export_file)

    if args.format == 'p1nvm':
        create_exported_data(export_file, version_resp)

    for data_type in data_types:
        export_msg = ExportDataMessage(data_type)
        if data_type == DataType.USER_CONFIG:
            if args.export_source.lower() == 'default':
                export_msg.source = ConfigurationSource.DEFAULT
            elif args.export_source.lower() == 'saved':
                export_msg.source = ConfigurationSource.SAVED
            else:
                export_msg.source = ConfigurationSource.ACTIVE

        while True:
            config_interface.send_message(export_msg)
            data_msg = config_interface.wait_for_message(MessageType.PLATFORM_STORAGE_DATA)
            if data_msg is None or not isinstance(data_msg, PlatformStorageDataMessage):
                logger.error('Device did not respond to export request.')
                return None
            # Check the response has the expected data type to avoid handling the periodic PlatformStorageDataMessage
            # output.
            if data_type == data_msg.data_type:
                break

        if data_msg.response == Response.NO_DATA_STORED:
            logger.info('No %s data store on the device.', data_type.name)
        elif data_msg.response == Response.DATA_CORRUPTED:
            logger.warning('%s data is corrupt.', data_type.name)
        elif data_msg.response != Response.OK:
            logger.warning('Export %s error: "%s"', data_type.name, data_msg.response.name)
            return None

        responses.append(data_msg)

        if args.format == 'p1nvm':
            add_to_exported_data(export_file, data_msg)
        else:
            with open(export_file, 'w') as fd:
                user_config = user_config_from_platform_storage(data_msg)
                if user_config:
                    fd.write(user_config.to_json())
                else:
                    return None

    logger.info('Exports successful.')
    return responses


def request_import(config_interface: DeviceInterface, args):
    file_extension = args.file.split('.')[-1].lower()
    data_types = _data_types_map[args.type]

    if file_extension == 'json':
        if args.type != 'user_config':
            logger.error('JSON import only valid for user_config. Use `--type=user_config`.')
            return False

        default_data = None
        # To preserve the current configuration and only override the values set in the JSON, first
        # load the current configuration from the device.
        if args.preserve_unspecified:
            logger.info('Reading current settings to preserve if unspecified.')
            while True:
                export_msg = ExportDataMessage(DataType.USER_CONFIG)
                config_interface.send_message(export_msg)
                data_msg = config_interface.wait_for_message(MessageType.PLATFORM_STORAGE_DATA)
                if data_msg is None or not isinstance(data_msg, PlatformStorageDataMessage):
                    logger.error('Device did not respond to export request.')
                    return False
                # Check the response has the expected data type to avoid handling the periodic PlatformStorageDataMessage
                # output.
                if DataType.USER_CONFIG == data_msg.data_type:
                    break
            if data_msg.response != Response.NO_DATA_STORED and data_msg.response != Response.OK:
                logger.warning('Export USER_CONFIG error: "%s"', data_msg.response.name)
                return False
            else:
                default_data = data_msg
        import_cmds = load_saved_json(args.file, DataType.USER_CONFIG, default_data)
    else:
        if args.preserve_unspecified:
            logger.error('`--preserve-unspecified` is only valid for JSON files.')
            return False
        elif not is_export_valid(args.file):
            logger.error('%s is not a valid data export.', args.file)
            return False
        else:
            import_cmds = load_saved_data(args.file, data_types)

    if len(import_cmds) == 0:
        logger.error('None of the data types %s found in %s.', [t.name for t in data_types], args.file)
        return False

    # Sort the data in the following order: user config, calibration, filter state. That way, if the calibration
    # changes, the loaded filter state should be consistent with it when the device hot starts the navigation engine.
    # Importing calibration data also performs a cold start, so if we load the filter state first, it will be lost.
    sort_order = {DataType.USER_CONFIG: 0, DataType.CALIBRATION_STATE: 1, DataType.FILTER_STATE: 2}
    import_cmds = sorted(import_cmds, key=lambda x: sort_order[x[0].data_type])

    actions = []
    for import_cmd, validity in import_cmds:
        actions.append(f'\t{import_cmd.data_type.name}: ' + {
            Response.NO_DATA_STORED: 'Exported data was empty. Will be cleared on device.',
            Response.OK: 'Overriding device with loaded value.',
            Response.DATA_CORRUPTED: 'Skipping corrupted data.'
        }[validity])

    logger.info('The following stored data on the device will be modified by the import:\n%s', '\n'.join(actions))

    if args.dry_run:
        return True

    if not args.force:
        user_input = input('Continue? (Y/n): ')
        if user_input.lower().startswith('n'):
            logger.warning('Halting import operation.')
            return False

    for import_cmd, validity in import_cmds:
        if validity == Response.DATA_CORRUPTED:
            continue

        sources = [ConfigurationSource.ACTIVE]

        if import_cmd.data_type == DataType.USER_CONFIG and not args.dont_save_config:
            sources.append(ConfigurationSource.SAVED)

        for source in sources:
            logger.info('Updating %s %s data on device.', source.name.lower(), import_cmd.data_type.name)
            import_cmd.source = source

            config_interface.send_message(import_cmd)

            resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
            if resp is None or not isinstance(resp, CommandResponseMessage):
                logger.error('Device did not respond to import request.')
                return False
            elif resp.response != Response.OK:
                logger.error('Import command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
                return False

    logger.info('Imports successful.')
    return True


def request_fault(config_interface: DeviceInterface, args):
    expect_response = True
    if args.fault == 'clear':
        logger.info('Sending a clear faults command.')
        payload = FaultControlMessage.ClearAll()
    elif args.fault == 'crash':
        logger.info('Sending a crash fault command.')
        payload = FaultControlMessage.Crash()
        expect_response = False
    elif args.fault == 'fatal':
        logger.info('Sending a fatal error fault command.')
        payload = FaultControlMessage.FatalError()
        expect_response = False
    elif args.fault == 'cocom':
        logger.info('Sending a COCOM fault command.')
        payload = FaultControlMessage.CoComLimit(_cocom_type[args.type])
    elif args.fault == 'gnss':
        logger.info('Sending a GNSS fault command.')
        payload = FaultControlMessage.EnableGNSS(args.enabled)
    elif args.fault == 'blackout':
        logger.info('Sending a blackout region fault command.')
        payload = FaultControlMessage.RegionBlackout(args.enabled)
    elif args.fault == 'quectel_test':
        logger.info(f'{"Enabling" if args.enabled else "Disabling"} Quectel test mode.')
        payload = FaultControlMessage.QuectelTest(args.enabled)
    elif args.fault == "integrity_status":
        logger.info('Sending a navigation engine integrity status fault command.')
        payload = FaultControlMessage.IntegrityStatus(args.type)
    else:
        logger.error('Unrecognized fault type.')
        return False

    config_interface.send_message(FaultControlMessage(payload))

    if expect_response:
        resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)
        if resp is None:
            logger.error('Response timed out after %d seconds.' % RESPONSE_TIMEOUT)
            return False
        elif resp.response != Response.OK:
            logger.error('Fault command rejected: %s (%d)' % (str(resp.response), int(resp.response)))
            return False
        else:
            logger.info('Command sent successfully.')
            return True
    else:
        logger.info('Command sent successfully (no response expected).')
        return True


def get_port_id(config_interface: DeviceInterface, args):
    interface = get_current_interface(config_interface)
    if interface is not None:
        name = None
        for k, v in INTERFACE_MAP.items():
            if v == interface:
                name = k
                break
        if name is None:
            logger.info('Unexpected interface %s reported.', str(interface))
        else:
            logger.info('Host port is connected to device interface %s.', name)
        return True
    else:
        return False


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]...' % execute_command,
        description='Update device lever arm configurations.',
        epilog="""\
EXAMPLE USAGE

Reset the device calibration and state:
    %(command)s reset calibration

Read the active lever arm values.
    %(command)s read

Apply changes to the GNSS and device (IMU) lever arms, then save the changes
to persistent storage.
    %(command)s apply gnss 0 0.4 1
    %(command)s apply device 0 2.1 1
    %(command)s save

Change the output lever arm, but do not save it to persistent storage. The
device will revert to the previous value after a power cycle.
    %(command)s apply output 0 0.4 1

Change the output lever arm, and save the new value immediately.
    %(command)s apply --save output 0 0.4 1

Change the UART1 baud rate, and save the new value immediately.
    %(command)s apply --save uart1 baud 115200

Change the UART1 output rate to 1 Hz for all messages (change not saved).
    %(command)s apply uart1 message_rate 1s

Change the UART1 output rate to 1 Hz for all NMEA messages (change not saved).
    %(command)s apply uart1 message_rate nmea 1s

Enable _all_ FusionEngine messages on UART1 with a 1 Hz rate (change not saved).
    %(command)s apply uart1 message_rate fe 1s --include-disabled

Read the current configuration for all message rates on UART2.
    %(command)s read uart2 message_rate

Read the current configuration for all FusionEngine message rates on UART2.
    %(command)s read uart2 message_rate fe

Read the current configuration for the NMEA GGA message rate on UART2.
    %(command)s read uart2 message_rate nmea gga

Disable GNSS for dead reckoning performance testing.
    %(command)s fault gnss off

Export the device's user configuration to a local file.
    %(command)s export user_config
""" % {'command': execute_command})

    parser.add_argument('--device-port', '--port', default="auto",
                        help="The serial device to use when communicating with the device.  If 'auto', the serial port "
                             "will be located automatically by searching for a connected device.")

    parser.add_argument('--device-tcp-address',
                        help="The address to use when communicating with the device over TCP. The address can be "
                             "specified with a port like 'address:port' to use a non-default port.")
    parser.add_argument('--device-websocket-address',
                        help="The address to use when communicating with the device over a websocket. The address can be "
                             "specified with a port like 'address:port' to use a non-default port.")
    parser.add_argument('--device-baud', '--baud', type=int, default=460800,
                        help="The baud rate used by the device serial port (--device-port).")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    command_subparsers = parser.add_subparsers(
        dest='command',
        help='The command to be run.')

    # Parent parser used to define arguments for both read and revert commands below.
    param_parser_parent = ArgumentParser(add_help=False)
    param_parser = param_parser_parent.add_subparsers(
        dest='param',
        help="The name of the parameters to be used. Leave blank to use all parameters.")

    # config_tool.py read
    help = 'Read the value of the specified parameter or set of parameters.'
    read_parser = command_subparsers.add_parser(
        'read',
        parents=[param_parser_parent],
        help=help,
        description="""\
%s

If no parameter name is specified, query the entire device configuration.

Example usage:
  config_tool.py read
  config_tool.py read --type=saved
  config_tool.py read gnss
  config_tool.py read uart1 message_rate fe
  config_tool.py read uart1 message_rate fe pose
  config_tool.py read uart1 message_rate fe gnss*
""" % help)

    read_parser.add_argument(
        '-t', '--type', metavar='TYPE', choices=['active', 'saved'], default='active',
        help="The type of settings to be queried:\n"
             "- active - Read the settings currently in use by the device\n"
             "- saved - Read the values saved to persistent storage, which will be restored on the next reboot\n")

    # config_tool.py revert
    help = 'Revert the value of the specified parameter or set of parameters back to its default value.'
    revert_parser = command_subparsers.add_parser(
        'revert',
        parents=[param_parser_parent],
        help=help,
        description="""\
%s

If no parameter name is specified, revert the entire device configuration.

Example usage:
  config_tool.py revert
  config_tool.py revert gnss
  config_tool.py revert uart1 message_rate fe
  config_tool.py revert uart1 message_rate fe pose
  config_tool.py revert uart1 message_rate fe gnss*
""" % help)

    revert_parser.add_argument(
        '-s', '--save', action=ExtendedBooleanAction,
        help="If set, save the new configuration to persistent storage.")

    revert_parser.add_argument('-f', '--include-disabled', action=ExtendedBooleanAction,
                               help='When reverting multiple messages, include the ones that are off.')

    # config_tool.py apply
    help = 'Change the value of the specified parameter, and optionally save the new value to persistent storage.'
    apply_parser = command_subparsers.add_parser(
        'apply',
        help=help,
        description="""\
%s

If --save is not specified, the new parameter value will take effect, but will
be reset back to its previous value if the device is rebooted. Saving to
persistent storage may be slow. When specifying multiple parameters, it is
recommended that you apply each value, and then issue a save command. For
example:
  config_tool.py apply device 0.3 -0.2 0.7
  config_tool.py apply gnss 0.0 0.5 1.2
  config_tool.py save
""" % help)

    apply_parser.add_argument(
        '-s', '--save', action=ExtendedBooleanAction,
        help="If set, the configuration will be saved after applying this value.")

    apply_param_parser = apply_parser.add_subparsers(dest='param', help="The name of the parameter to be modified.")

    # config_tool.py apply -- lever arms and device orientation
    help = 'The GNSS antenna lever arm (in meters).'
    param_parser.add_parser('gnss', help=help, description=help)
    gnss_parser = apply_param_parser.add_parser('gnss', help=help, description=help)
    gnss_parser.add_argument('x', type=float, help='The X offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('y', type=float, help='The Y offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('z', type=float, help='The Z offset with respect to the vehicle body (in meters).')

    help = 'The device (IMU) lever arm (in meters).'
    param_parser.add_parser('device', help=help, description=help)
    gnss_parser = apply_param_parser.add_parser('device', help=help, description=help)
    gnss_parser.add_argument('x', type=float, help='The X offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('y', type=float, help='The Y offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('z', type=float, help='The Z offset with respect to the vehicle body (in meters).')

    help = 'The location of the desired output location with respect to the vehicle body frame.'
    param_parser.add_parser('output', help=help, description=help)
    gnss_parser = apply_param_parser.add_parser('output', help=help, description=help)
    gnss_parser.add_argument('x', type=float, help='The X offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('y', type=float, help='The Y offset with respect to the vehicle body (in meters).')
    gnss_parser.add_argument('z', type=float, help='The Z offset with respect to the vehicle body (in meters).')

    help = 'The heading horizontal and vertical biases (in degrees).'
    param_parser.add_parser('heading_bias', help=help, description=help)
    heading_parser = apply_param_parser.add_parser('heading_bias', help=help, description=help)
    heading_parser.add_argument('horizontal', type=float, help='The horizontal bias (yaw) in degrees.')
    heading_parser.add_argument('vertical', type=float, help='The vertical bias (pitch) in degrees.')

    help = 'The orientation of the device (IMU) within the vehicle, specified using the directions of the device +X ' \
           'and +Z axes relative to the vehicle body axes (+X = forward, +Y = left, +Z = up).'
    param_parser.add_parser('orientation', help=help, description=help)
    orientation_parser = apply_param_parser.add_parser('orientation', help=help, description=help)
    orientation_parser.add_argument('x', choices=_orientation_map.keys(),
                                    help='The orientation of the IMU +X axis relative to the vehicle body axes.')
    orientation_parser.add_argument('z', choices=_orientation_map.keys(), nargs='?', default='up',
                                    help='The orientation of the IMU +Z axis relative to the vehicle body axes.')

    # config_tool.py apply -- enabled GNSS systems/frequencies
    help = 'Enable/disable use of the specified GNSS systems.'
    param_parser.add_parser('gnss_systems', help=help, description=help)
    enable_gnss_parser = apply_param_parser.add_parser(
        'gnss_systems',
        help=help, description=help, epilog="""\
EXAMPLE USAGE

Disable BeiDou:
    %(command)s apply gnss_systems beidou off

Enable all GNSS constellations supported by the device:
    %(command)s apply gnss_systems all on

Enable only GPS and Galileo:
    %(command)s apply gnss_systems gps,galileo only
""")
    enable_gnss_parser.add_argument(
        'systems', choices=[str(s).lower() for s in SatelliteTypeMask if s != 'UNKNOWN'],
        help='A comma-separated list of GNSS systems to be enabled/disabled.')
    enable_gnss_parser.add_argument(
        'action', choices=['on', 'off', 'only'],
        help='The action to perform:\n'
             '- on - Enable the specified systems\n'
             '- off - Disable the specified systems\n'
             '- only - Enable only the specified systems, disable all others')

    help = 'Enable/disable use of the specified GNSS frequency bands.'
    param_parser.add_parser('gnss_frequencies', help=help, description=help)
    enable_freq_parser = apply_param_parser.add_parser(
        'gnss_frequencies',
        help=help, description=help, epilog="""\
EXAMPLE USAGE

Disable L5:
    %(command)s apply gnss_frequencies l5 off

Enable all GNSS frequency bands supported by the device:
    %(command)s apply gnss_frequencies all on

Enable only L1 and L5:
    %(command)s apply gnss_frequencies l1,l5 only
""")
    enable_freq_parser.add_argument(
        'frequencies', choices=[str(s).lower() for s in FrequencyBandMask if s != 'UNKNOWN'],
        help='A comma-separated list of GNSS frequency bands to be enabled/disabled.')
    enable_freq_parser.add_argument(
        'action', choices=['on', 'off', 'only'],
        help='The action to perform:\n'
             '- on - Enable the specified frequency bands\n'
             '- off - Disable the specified frequency bands\n'
             '- only - Enable only the specified frequency bands, disable all others')

    help = 'Manually specify the UTC leap second.'
    param_parser.add_parser('leap_second', help=help, description=help)
    leap_second_parser = apply_param_parser.add_parser(
        'leap_second',
        help=help, description=f"""\
{help}

The specified value will be used instead of any current or future leap second
information decoded from the incoming GNSS signals.
""")
    leap_second_parser.add_argument(
        'value',
        help='The UTC leap second count to use. Set to -1 to disable manual override.')

    help = 'Manually specify the GPS legacy week rollover count.'
    param_parser.add_parser('gps_week_rollover', help=help, description=help)
    gps_week_rollover_parser = apply_param_parser.add_parser(
        'gps_week_rollover',
        help=help, description=f"""\
{help}

The specified value will be used instead of any date information available from
other sources (non-GPS signals, modernized GPS navigation messages, etc.).
""")
    gps_week_rollover_parser.add_argument(
        'value',
        help='The GPS legacy week rollover count to use. Set to -1 to disable manual override.')

    help = 'Set the ionospheric delay model.'
    param_parser.add_parser('ionosphere_config', help=help, description=help)
    ionosphere_config_parser = apply_param_parser.add_parser('ionosphere_config',
                                                             help=help, description=help)
    ionosphere_config_parser.add_argument('--iono-delay-model', '--iono-model',
                                          choices=_iono_delay_model_map.keys(),
                                          help='The ionospheric delay model to be used.')

    help = 'Set the tropospheric delay model.'
    param_parser.add_parser('troposphere_config', help=help, description=help)
    troposphere_config_parser = apply_param_parser.add_parser('troposphere_config',
                                                                    help=help, description=help)
    troposphere_config_parser.add_argument('--tropo-delay-model', '--tropo-model',
                                                 choices=_tropo_delay_model_map.keys(),
                                                 help='The tropospheric delay model to be used.')

    # config_tool.py apply -- vehicle details
    help = 'Set vehicle model and dimensions.'
    param_parser.add_parser('vehicle_details', help=help, description=help)
    vehicle_details_parser = apply_param_parser.add_parser('vehicle_details', help=help, description="""\
%s

Any omitted arguments will retain their previous values.""" % help)
    vehicle_details_parser.add_argument('--vehicle-model', '--model', choices=_vehicle_model_map.keys(),
                                        help='Specify the type of vehicle (used to enable CAN message decoding).')
    vehicle_details_parser.add_argument('--wheelbase', type=float,
                                        help='The distance between the front axle and rear axle (in meters).')
    vehicle_details_parser.add_argument('--front-track-width', '--front-track', type=float,
                                        help='The distance between the two front wheels (in meters).')
    vehicle_details_parser.add_argument('--rear-track-width', '--rear-track', type=float,
                                        help='The distance between the two rear wheels (in meters).')

    # config_tool.py apply -- wheel speed configuration
    help = 'Configure software wheel speed/tick support.'
    param_parser.add_parser('wheel_config', help=help, description=help)
    wheel_config_parser = apply_param_parser.add_parser('wheel_config', help=help, description=f'''\
{help}

Any parameters that are not specified will be left unchanged and will continue
using their existing values.''')
    wheel_config_parser.add_argument('--wheel-sensor-type', choices=_wheel_sensor_type_map.keys(),
                                     help='The type of vehicle/wheel speed measurements produced by the vehicle.')
    wheel_config_parser.add_argument('--applied-speed-type', choices=_applied_speed_type_map.keys(),
                                     help='The type of vehicle/wheel speed measurements to be applied.')
    wheel_config_parser.add_argument('--steering-type', choices=_steering_type_map.keys(),
                                     help='Indication of which of the vehicle\'s wheels are steered.')
    wheel_config_parser.add_argument('--wheel-update-interval', type=float,
                                     help='The nominal rate at which wheel speed/tick measurements will be input (in '
                                          'seconds).')
    wheel_config_parser.add_argument('--wheel-tick-output-interval', type=float,
                                     help='Override the rate at which the device will calculate wheel speed values '
                                          'from incoming wheel tick data (in seconds). By default, the device will '
                                          'select the best value automatically based on the tick quanitzation '
                                          '(meters/tick scale factor). We do _not_ recommend setting this value for '
                                          'most configurations.')
    wheel_config_parser.add_argument('--steering-ratio', type=float,
                                     help='Ratio between angle of the steering wheel and the angle of the wheels on '
                                          'the ground.')
    wheel_config_parser.add_argument('--meters-per-tick', '--wheel-ticks-to-m', type=float,
                                     help='The scale factor to convert from wheel encoder ticks to distance (in '
                                          'meters/tick).')
    wheel_config_parser.add_argument('--wheel-tick-max-value', type=int,
                                     help='The maximum value (inclusive) before the wheel tick measurement will roll '
                                          'over.')
    wheel_config_parser.add_argument('--wheel-ticks-signed', action=TriStateBooleanAction,
                                     help='True if the reported wheel tick measurements should be interpreted as '
                                          'signed integers, or false if they should be interpreted as unsigned '
                                          'integers.')
    wheel_config_parser.add_argument('--wheel-ticks-always-increase', action=TriStateBooleanAction,
                                     help='True if the wheel tick measurements increase by a positive amount when '
                                          'driving forward or backward. False if wheel tick measurements decrease when '
                                          'driving backward.')

    help = 'Configure hardware wheel encoder tick support.'
    param_parser.add_parser('hardware_tick_config', help=help, description=help)
    hardware_tick_config_parser = apply_param_parser.add_parser('hardware_tick_config', help=help, description=help)
    hardware_tick_config_parser.add_argument('--tick-mode', help='Indication of whether ticks are being measured.',
                                             choices=_tick_mode_map.keys())
    hardware_tick_config_parser.add_argument('--tick-direction', help='Directions in which ticks increase.',
                                             choices=_tick_direction_map.keys())
    hardware_tick_config_parser.add_argument('--meters-per-tick', '--wheel-ticks-to-m', type=float,
                                             help='The scale factor to convert from wheel encoder ticks to distance '
                                                  '(in meters/tick).')

    # config_tool.py apply -- output interface/stream control
    help = 'Configure the UART1 serial baud rate.'
    param_parser.add_parser('uart1_baud', help=help, description=help)
    uart_1_baud_parser = apply_param_parser.add_parser('uart1_baud', help=help, description=help)
    uart_1_baud_parser.add_argument('baud_rate', type=float,
                                    help='The desired baud rate (in bits/second).')

    help = 'Configure the UART2 serial baud rate.'
    param_parser.add_parser('uart2_baud', help=help, description=help)
    uart_2_baud_parser = apply_param_parser.add_parser('uart2_baud', help=help, description=help)
    uart_2_baud_parser.add_argument('baud_rate', type=float,
                                    help='The desired baud rate (in bits/second).')

    help = 'Enable/disable output for all diagnostics messages on UART1.'
    param_parser.add_parser('uart1_diagnostics_enabled', help=help, description=help)
    uart_1_diagnostics_enabled_parser = apply_param_parser.add_parser(
        'uart1_diagnostics_enabled', help=help, description=help)
    uart_1_diagnostics_enabled_parser.add_argument(
        'enabled', action=ExtendedBooleanAction, help='Enable/disable diagnostic messages.')

    help = 'Enable/disable output for all diagnostics messages on UART2.'
    param_parser.add_parser('uart2_diagnostics_enabled', help=help, description=help)
    uart_2_diagnostics_enabled_parser = apply_param_parser.add_parser(
        'uart2_diagnostics_enabled', help=help, description=help)
    uart_2_diagnostics_enabled_parser.add_argument(
        'enabled', action=ExtendedBooleanAction, help='Enable/disable diagnostic messages.')

    help = 'Enable/disable the watchdog timer reset after fatal errors.'
    param_parser.add_parser('watchdog_enabled', help=help, description=help)
    watchdog_enabled_parser = apply_param_parser.add_parser(
        'watchdog_enabled', help=help, description=help)
    watchdog_enabled_parser.add_argument(
        'enabled', action=ExtendedBooleanAction,
        help='Enable/disable the watchdog timer reset after fatal errors.')

    help = 'Set the user device ID (max 32 characters).'
    param_parser.add_parser('user_device_id', help=help, description=help)
    user_device_id_parser = apply_param_parser.add_parser(
        'user_device_id', help=help, description=help)
    user_device_id_parser.add_argument(
        'device_id',
        help='Set the user device ID (max 32 characters).')

    INTERFACES_WITH_EXPLICIT_ENUM = ['current', 'uart1', 'uart2']
    for interface_name, interface_id in sorted(INTERFACE_MAP.items()):
        supported_fe_messages = '\n'.join([
            f'  - {message_type_to_class[m].__name__} ({int(m)})'
            for m in MessageType
            if m in message_type_to_class and not str(m).endswith('INPUT')
        ])
        supported_nmea_messages = '\n'.join([f'  - {m}' for m in NmeaMessageType if m.name != 'INVALID'])
        if interface_name == 'current':
            port_description = 'the device UART connected to the current --device-port'
        else:
            port_description = f'serial UART{interface_name[-1]}'

        # config_tool.py read INTERFACE_NAME
        help = f'Read configuration for {interface_name}.'
        read_interface_config_parser = param_parser.add_parser(
            interface_name,
            help=help,
            description=help)

        # config_tool.py apply INTERFACE_NAME
        help = f'Apply configuration for {interface_name}.'
        apply_interface_config_parsers = apply_param_parser.add_parser(
            interface_name,
            help=help,
            description=help)

        # config_tool.py read INTERFACE_NAME CONFIG_TYPE
        read_interface_config_type_parsers = read_interface_config_parser.add_subparsers(
            dest='interface_config_type', help='The name of the parameter to be read.')

        # config_tool.py apply INTERFACE_NAME CONFIG_TYPE PARAMS
        apply_interface_config_type_parsers = apply_interface_config_parsers.add_subparsers(
            dest='interface_config_type',
            help='The name of the parameter to be modified.')

        # Parent parser for reading interface rates.
        read_output_rate_parser = ArgumentParser(add_help=False)
        # Parent parser for applying interface rates.
        apply_output_rate_parser = ArgumentParser(add_help=False)

        help = """\
The message protocol name:
- all - All messages on all protocols for an interface
- fe, fusion_engine - Point One FusionEngine protocol
- nmea - NMEA-0183
- rtcm - RTCM 10403.3"""
        read_output_rate_parser.add_argument('protocol', metavar="PROTOCOL", nargs='?', default='all', help=help)
        apply_output_rate_parser.add_argument('protocol', metavar="PROTOCOL", help=help)

        help = 'The message type (name) or ID (integer). Use "all" to request all messages for the specified ' \
               'protocol. Requests may contain a comma-separated list with multiple message names, or may use ' \
               'wildcards (*) to match multiple messages by name (e.g., "gnss*" to match GNSSInfo and GNSSSatellite).'
        read_output_rate_parser.add_argument('message_id', metavar="ID", nargs='?', default='all', help=help)
        apply_output_rate_parser.add_argument('message_id', metavar="ID", nargs='?', default=None, help=help)

        apply_output_rate_parser.add_argument('rate', metavar="RATE", nargs='?', default=None,
                                              help='The desired message rate:%s' %
                                              ''.join(['\n- %s' % n for n in MESSAGE_RATE_MAP]))
        apply_output_rate_parser.add_argument('-f', '--include-disabled', action=ExtendedBooleanAction,
                                              help='When setting multiple messages, include the ones that are off.')

        help = f'Query the output rate for a specified message type or protocol.'
        epilog = f'''\
FusionEngine message types:
{supported_fe_messages}

NMEA message types:
{supported_nmea_messages}'''
        message_rate_description = f'''\
{help}

When reading the rate for an individual message type, you must specify the
protocol (FusionEngine, NMEA, RTCM) and message ID. If you omit either
parameter, the rate will be queried for all message types/protocols.

Protocol and message names are not case sensitive. The message name specifier
may contain a comma-separated list to specify more than one message type.
Additionally, you can specify a wildcard (*) to match multiple message types.
For FusionEngine messages, you may enter either a numeric message ID, or a
partial or complete message name (e.g., imu, IMU, IMUMeasurement, or 11000).

Example usage:
  config_tool.py read {interface_name} message_rate          # Read all message rates
  config_tool.py read {interface_name} message_rate nmea gga # Read NMEA GGA rate
  config_tool.py read {interface_name} message_rate nmea gga,rmc # Read NMEA GGA/RMC rates
  config_tool.py read {interface_name} message_rate fe       # Read all FusionEngine rates
  config_tool.py read {interface_name} message_rate fe imu   # Read FusionEngine IMU rate
  config_tool.py read {interface_name} message_rate fe 11000 # Read FusionEngine IMU rate
  config_tool.py read {interface_name} message_rate fe gnss* # Read all GNSS message rates'''

        if interface_name in INTERFACES_WITH_EXPLICIT_ENUM:
            # config_tool.py read uartN_message_rate
            param_parser.add_parser(
                f'{interface_name}_message_rate',
                help=help,
                parents=[read_output_rate_parser],
                description=message_rate_description,
                epilog=epilog)

        # config_tool.py read INTERFACE_NAME message_rate
        read_interface_config_type_parsers.add_parser(
            'message_rate', parents=[read_output_rate_parser],
            help=help, description=message_rate_description, epilog=epilog)

        help = f'Configure the output rate for a specified message type or protocol.'
        message_rate_description = f'''\
{help}

When setting the rate for an individual message type, you must specify the
protocol (FusionEngine, NMEA, RTCM) and message ID. If you omit either
parameter, the rate will be set for all message types/protocols.

When setting the rate for individual message types, you must specify the
protocol (FusionEngine, NMEA, RTCM) and message ID. Protocol and message names
are not case sensitive. The message name specifier may contain a
comma-separated list to specify more than one message type. Additionally, you
can specify a wildcard (*) to match multiple message types. For FusionEngine
messages, you may enter either a numeric message ID, or a partial or complete
message name (e.g., imu, IMU, IMUMeasurement, or 11000).

When setting the rate for multiple message types -- all messages from one
protocol, or all protocols -- by default, the new value will ONLY apply for
messages that are already enabled. This is done to prevent unexpectedly
enabling messages when trying to change the rate of the current output. If you
wish to enable all messages, use the --include-disabled argument.

Example usage:
  config_tool.py apply {interface_name} message_rate nmea 1s
  config_tool.py apply {interface_name} message_rate nmea gga 1s
  config_tool.py apply {interface_name} message_rate nmea gga,rmc 1s
  config_tool.py apply {interface_name} message_rate fe 1s
  config_tool.py apply {interface_name} message_rate fe 1s --include-disabled
  config_tool.py apply {interface_name} message_rate fe gnss* 1s
  config_tool.py apply {interface_name} message_rate fe imu on_change
  config_tool.py apply {interface_name} message_rate fe 11000 on_change'''
        epilog = f'''\
FusionEngine message types:
{supported_fe_messages}

NMEA message types:
{supported_nmea_messages}'''

        if interface_name in INTERFACES_WITH_EXPLICIT_ENUM:
            # config_tool.py apply uartN_message_rate
            apply_param_parser.add_parser(
                f'{interface_name}_message_rate',
                parents=[apply_output_rate_parser],
                help=help,
                description=message_rate_description,
                epilog=epilog)

        # config_tool.py apply INTERFACE_NAME message_rate
        apply_interface_config_type_parsers.add_parser(
            'message_rate', parents=[apply_output_rate_parser],
            help=help, description=help, epilog=epilog)

        # config_tool.py apply INTERFACE_NAME diagnostics_enabled
        help = 'Enable/disable output for all diagnostics messages.'
        read_interface_config_type_parsers.add_parser('diagnostics_enabled', help=help, description=help)
        diagnostics_enabled_parser = apply_interface_config_type_parsers.add_parser(
            'diagnostics_enabled', help=help, description=help)
        diagnostics_enabled_parser.add_argument(
            'enabled', action=ExtendedBooleanAction, help='Enable/disable diagnostic messages.')

        # config_tool.py apply INTERFACE_NAME enabled
        help = 'Configure if the interface is enabled.'
        read_interface_config_type_parsers.add_parser('enabled', help=help, description=help)
        baud_rate_parser = apply_interface_config_type_parsers.add_parser('enabled', help=help, description=help)
        baud_rate_parser.add_argument('enabled', action=ExtendedBooleanAction,
                                      help='Configure if the interface is enabled.')

        if interface_id.type in (TransportType.SERIAL, TransportType.CURRENT):
            # config_tool.py apply INTERFACE_NAME baud_rate
            help = 'Configure the serial baud rate.'
            read_interface_config_type_parsers.add_parser('baud_rate', help=help, description=help)
            baud_rate_parser = apply_interface_config_type_parsers.add_parser('baud_rate', help=help, description=help)
            baud_rate_parser.add_argument('baud_rate', type=int,
                                          help='The desired baud rate (in bits/second).')

        if interface_id.type in (TransportType.UDP, TransportType.TCP, TransportType.CURRENT):
            # config_tool.py apply INTERFACE_NAME port
            help = 'Configure the network port.'
            read_interface_config_type_parsers.add_parser(
                'port', help=help, description=help)
            baud_rate_parser = apply_interface_config_type_parsers.add_parser(
                'port', help=help, description=help)
            baud_rate_parser.add_argument('port', type=int,
                                          help='The desired network port.')

        if interface_id.type in (TransportType.UDP, TransportType.UNIX, TransportType.CURRENT):
            # config_tool.py apply INTERFACE_NAME remote_address
            help = 'Configure the remote hostname or IP address, or the socket file path for UNIX domain sockets.'
            read_interface_config_type_parsers.add_parser('remote_address', help=help, description=help)
            baud_rate_parser = apply_interface_config_type_parsers.add_parser('remote_address', help=help,
                                                                              description=help)
            baud_rate_parser.add_argument('remote_address', type=str,
                                          help='The address to connect to.')

    # config_tool.py copy_message_config
    help = 'Copy the output message configuration from one interface to another.'
    copy_parser = command_subparsers.add_parser(
        'copy_message_config',
        help=help,
        description=f'''\
{help}

Note: This command copies message rates and diagnostic output status. It does
not copy interface parameters such as baud rate or TCP port.

Example usage:
  config_tool.py copy_message_config uart2 uart1 # Copy UART2's configuration to UART1
''')
    copy_parser.add_argument(
        '-d', '--diagnostics-enabled', '--diag', action=ExtendedBooleanAction, default=True,
        help="If set, copy the diagnostic output state from the source interface.")
    copy_parser.add_argument(
        '-m', '--message-rates', '--rate', action=ExtendedBooleanAction, default=True,
        help="If set, copy the message rates from the source interface.")
    copy_parser.add_argument(
        '-s', '--save', action=ExtendedBooleanAction,
        help="If set, save the new configuration to persistent storage.")
    copy_parser.add_argument(
        'source',
        help='The name of the source interface: %s' % ', '.join(sorted(INTERFACE_MAP.keys())))
    copy_parser.add_argument(
        'dest',
        help='The name of the destination interface: %s' % ', '.join(sorted(INTERFACE_MAP.keys())))

    # config_tool.py fault
    help = 'Apply system fault controls.'
    fault_parser = command_subparsers.add_parser(
        'fault',
        help=help,
        description=help)

    type_parser = fault_parser.add_subparsers(dest='fault', help="The type of fault to be applied.")

    crash_parser = type_parser.add_parser(
        'clear',
        help='Clear existing faults.')

    crash_parser = type_parser.add_parser(
        'crash',
        help='Force the device to crash.')

    fatal_parser = type_parser.add_parser(
        'fatal',
        help='Force the device to exhibit a fatal error.')

    cocom_parser = type_parser.add_parser(
        'cocom',
        help='Simulate a COCOM limit.')
    choices = list(_cocom_type.keys())
    cocom_parser.add_argument(
        'type', metavar='TYPE',
        choices=choices,
        default='acceleration',
        help="The type of COCOM limit to be simulated: %s." % ', '.join(choices))

    gnss_parser = type_parser.add_parser(
        'gnss',
        help='Enable/disable use of GNSS measurements.')
    gnss_parser.add_argument(
        'enabled', action=ExtendedBooleanAction,
        help='Enable/disable GNSS measurements.')

    blackout_parser = type_parser.add_parser(
        'blackout',
        help='Enable/disable applying a simulated blackout region.')
    blackout_parser.add_argument(
        'enabled', action=ExtendedBooleanAction,
        help='Enable/disable simulated blackout region.')

    quectel_test_parser = type_parser.add_parser(
        'quectel_test',
        help='Enable/disable Quectel test mode.')
    quectel_test_parser.add_argument(
        'enabled', action=ExtendedBooleanAction,
        help='Enable/disable Quectel test mode.')

    integrity_status_parser = type_parser.add_parser(
        'integrity_status',
        help="Simulate a navigation engine integrity failure.")
    integrity_status_parser.add_argument('type', type=int, help='Type of integrity failure.')

    # config_tool.py reset
    help = 'Issue a device reset request.'
    reset_parser = command_subparsers.add_parser(
        'reset',
        help=help,
        description=help)

    choices = {
        'factory': 'Reset all settings back to factory defaults.',
        'hot': 'Perform a hot start, keeping GNSS ephemeris data, and approximate position/time knowledge where '
               'applicable',
        'warm': 'Perform a warm start, resetting GNSS ephemeris data, and approximate position/time knowledge where '
                'applicable',
        'pvt': 'Reset all position, velocity, orientation, time, and GNSS corrrections information',
        'cold': 'Perform a cold start, resetting position/time information and GNSS ephemeris/corrections data',
        'diag': 'Reset to a deterministic state for diagnostic and post-processing purposes',
        'calibration': 'Reset all calibration and navigation state data',
        'config': 'Reset all user configuration data (this will also reset calibration and navigation state data)',
        'nav_engine': 'Reset the navigation engine: clear all PVT data, plus sensor corrections (IMU and wheel speed)',
        'position': 'Reset position, velocity, and orientation data',
        'reboot': 'Reboot the navigation processor',
        'reboot_gnss': 'Reboot the GNSS measurement engine',
    }
    newline = '\n'
    reset_parser.add_argument(
        'type', metavar='TYPE',
        choices=choices.keys(),
        nargs='+',
        default='cold',
        help=f"""\
The type of reset to be performed: {''.join([f'{newline}- {k} - {v}' for k, v in choices.items()])}

You may specify more than one value to reset multiple components. For example, to perform a warm start and also a
diagnostic log reset:
  {execute_command} reset diag cold""")

    # config_tool.py export
    help = 'Export data from the device to a local file.'
    export_parser = command_subparsers.add_parser(
        'export',
        help=help,
        description=help)

    export_parser.add_argument(
        "--export-file", default=None,
        help='The file name to save data exported from the device to.')
    export_parser.add_argument(
        '--type',
        choices=_data_types_map.keys(),
        default='all',
        help="The type of data to export to a local file: %s" % ', '.join(_data_types_map.keys()))
    export_parser.add_argument(
        '--format',
        choices=['json', 'p1nvm'],
        default='p1nvm',
        help="The format for the exported file. json is only supported for user_config export.")
    export_parser.add_argument(
        '--export-source',
        choices=['active','saved', 'default'],
        default='active',
        help="When exporting the user_config, should the exported data come from the active configuration, the value saved to storage, or the device defaults.")

    # config_tool.py import
    help = 'Import data from a local file to the device.'
    import_parser = command_subparsers.add_parser(
        'import',
        help=help,
        description=help)

    import_parser.add_argument(
        '--dont-save-config', action=ExtendedBooleanAction,
        help="If set, the user_config data will not be saved after importing.")
    import_parser.add_argument(
        '--dry-run', action=ExtendedBooleanAction,
        help="If set, print the actions that the import would take, but don't modify the device.")
    import_parser.add_argument(
        '-f', '--force', action=ExtendedBooleanAction,
        help="If set, the user will not be prompted to confirm any actions.")
    import_parser.add_argument(
        '--preserve-unspecified', action=ExtendedBooleanAction,
        help="""\
If set, only modify the values specified in the JSON. This flag is only valid
when importing a JSON file. If this is flag isn't specified, values not set in
the JSON will be set to their default values.""")
    import_parser.add_argument(
        '--type',
        choices=_data_types_map.keys(),
        default='all',
        help="The type of data to send to the device: %s" % ', '.join(_data_types_map.keys()))
    import_parser.add_argument(
        'file', metavar='FILE',
        help="The file containing data to send to the device.")

    # config_tool.py export_file_info
    help = 'List the contents of an export file.'
    export_file_info_parser = command_subparsers.add_parser(
        'export_file_info',
        help=help,
        description=help)

    export_file_info_parser.add_argument(
        'file', metavar='FILE',
        help="The file containing the exported data.")

    # config_tool.py shutdown
    help = 'Issue a navigation engine shutdown request.'
    shutdown_parser = command_subparsers.add_parser(
        'shutdown',
        help=help,
        description=help)
    choices = {
        'engine': 'Shutdown the engine',
        'log': 'Stop the current log',
    }
    newline = '\n'
    shutdown_parser.add_argument(
        'type', metavar='TYPE',
        nargs='?',
        choices=choices.keys(),
        default='engine',
        help=f"""\
The type of shutdown to be performed: {''.join([f'{newline}- {k} - {v}' for k, v in choices.items()])}""")

    # config_tool.py startup
    help = 'Issue a navigation engine startup request.'
    startup_parser = command_subparsers.add_parser(
        'startup',
        help=help,
        description=help)
    choices = {
        'engine': 'Startup the engine',
        'log': 'Start a new log',
    }
    newline = '\n'
    startup_parser.add_argument(
        'type', metavar='TYPE',
        choices=choices.keys(),
        nargs='?',
        default='engine',
        help=f"""\
The type of startup to be performed: {''.join([f'{newline}- {k} - {v}' for k, v in choices.items()])}""")

    # config_tool.py save
    help = 'Save the active config so the values to persist through power cycle.'
    save_parser = command_subparsers.add_parser(
        'save',
        help=help,
        description=help)
    revert_group = save_parser.add_mutually_exclusive_group()
    revert_group.add_argument(
        '-r', '--revert-to-saved', action=ExtendedBooleanAction,
        help="If set, revert the active configuration to the saved values.")
    revert_group.add_argument(
        '-d', '--revert-to-defaults', action=ExtendedBooleanAction,
        help="If set, revert the active configuration to the factory defaults.")

    # config_tool.py system_status
    help = 'Query the device system status information.'
    system_status_parser = command_subparsers.add_parser(
        'system_status',
        help=help,
        description=help)

    # config_tool.py device_id
    help = 'Query the device system status information.'
    device_id_parser = command_subparsers.add_parser(
        'device_id',
        help=help,
        description=help)

    # config_tool.py version
    help = 'Query the device version information.'
    version_parser = command_subparsers.add_parser(
        'version',
        help=help,
        description=help)

    version_parser.add_argument(
        '-t', '--type', metavar='TYPE', choices=['fusion_engine', 'nmea'], default='fusion_engine',
        help="The type of version message to be queried: fusion_engine, nmea")

    # config_tool.py get_port_id
    help = 'Query which device interface corresponds to the host --device-port used.'
    get_port_id_parser = command_subparsers.add_parser(
        'get_port_id',
        help=help,
        description=help)

    # config_tool.py list_interfaces
    help = 'List the IO interfaces supported by the device.'
    list_interfaces_parser = command_subparsers.add_parser(
        'list_interfaces',
        help=help,
        description=help)

    args = parser.parse_known_args()
    args = parser.parse_args(args[1], args[0])

    if args.verbose == 0:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            stream=sys.stdout)
        logger.setLevel(logging.DEBUG)
        if args.verbose == 1:
            logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.DEBUG)
        else:
            logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.TRACE)

    if args.command is None:
        logger.error('No command specified.\n')
        parser.print_help()
        sys.exit(1)

    if 'interface_config_type' in args and args.interface_config_type is None:
        logger.error('No interface config type specified.\n')
        parser.print_help()
        sys.exit(1)

    if args.device_websocket_address is not None:
        if websocket_connect is None:
            logger.error('Websocket support not available.')
            sys.exit(1)
        else:
            try:
                websocket = websocket_connect('ws://' + args.device_websocket_address)
            except Exception as e:
                logger.error("Problem connecting to Websocket address '%s': %s." %
                             (args.device_websocket_address, str(e)))
                sys.exit(1)
            data_source = WebSocketDataSource(websocket)
    elif args.device_tcp_address is not None:
        try:
            parts = args.device_tcp_address.split(':')
            address = parts[0]
            if len(parts) == 1:
                port = DEFAULT_TCP_PORT
            elif len(parts) == 2:
                port = int(parts[1])
            else:
                logger.error('Invalid websocket address.')
                sys.exit(1)

            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((address, port))
        except Exception as e:
            logger.error("Problem connecting to TCP address '%s': %s." % (args.device_tcp_address, str(e)))
            sys.exit(1)
        data_source = SocketDataSource(s)
    else:
        # Note: We intentionally use the Enhanced port here, whereas p1_runner uses Standard port. That way users can
        # issue configuration requests while the device is active and p1_runner is operating. If the user explicitly
        # sets --device-port, we'll use that port regardless of type.
        device_port = find_serial_device(port_name=args.device_port, port_type=PortType.ENHANCED,
                                         on_wrong_type='none')
        logger.info('Connecting to device using serial port %s.' % device_port)
        serial_port = serial.Serial(port=device_port, baudrate=args.device_baud, timeout=SERIAL_TIMEOUT)
        data_source = SerialDataSource(serial_port)
        data_source.start_read_thread()

    config_interface = DeviceInterface(data_source)

    passed = False

    if args.command == "read":
        passed = read_config(config_interface, args)
    elif args.command == "revert":
        passed = revert_config(config_interface, args)
    elif args.command == "apply":
        passed = apply_config(config_interface, args)
    elif args.command == "save":
        passed = save_config(config_interface, args)
    elif args.command == "copy_message_config":
        passed = copy_interface_message_config(config_interface, args)
    elif args.command == "version":
        passed = query_version(config_interface, args)
    elif args.command == "system_status":
        passed = query_system_status(config_interface, args)
    elif args.command == "device_id":
        passed = query_device_id(config_interface, args)
    elif args.command == "reset":
        passed = request_reset(config_interface, args)
    elif args.command == "shutdown":
        passed = request_shutdown(config_interface, args)
    elif args.command == "startup":
        passed = request_startup(config_interface, args)
    elif args.command == "fault":
        passed = request_fault(config_interface, args)
    elif args.command == "export":
        passed = request_export(config_interface, args)
    elif args.command == "import":
        passed = request_import(config_interface, args)
    elif args.command == "export_file_info":
        vars(args)['dry_run'] = True
        vars(args)['type'] = "all"
        passed = request_import(config_interface, args)
    elif args.command == "get_port_id":
        passed = get_port_id(config_interface, args)
    elif args.command == "list_interfaces":
        passed = query_interfaces(config_interface, args)
    else:
        logger.error("Unrecognized command '%s'." % args.command)

    data_source.stop()

    if passed:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
