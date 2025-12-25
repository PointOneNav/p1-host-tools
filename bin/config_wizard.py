#!/usr/bin/env python3

"""
Interactive configuration wizard for Point One devices.

This wizard guides users through configuring key device parameters:
- IMU to body lever arm (X, Y, Z)
- GPS to body lever arm (X, Y, Z)
- Device orientation (Z axis direction, X axis direction)
"""

import os
import socket
import sys
from urllib.parse import urlparse

from fusion_engine_client.messages import *

# Add the parent directory to the search path to enable p1_runner imports.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
sys.path.append(os.path.dirname(__file__))

from p1_runner import trace as logging
from p1_runner.data_source import SocketDataSource
from p1_runner.device_interface import DeviceInterface

logger = logging.getLogger('point_one.config_wizard')

DEFAULT_TCP_PORT = 30200

# Direction options for orientation
DIRECTION_OPTIONS = {
    'forward': Direction.FORWARD,
    'backward': Direction.BACKWARD,
    'left': Direction.LEFT,
    'right': Direction.RIGHT,
    'up': Direction.UP,
    'down': Direction.DOWN,
}

DIRECTION_NAMES = {v: k for k, v in DIRECTION_OPTIONS.items()}


def get_direction_name(direction: Direction) -> str:
    """Convert Direction enum to human-readable name."""
    return DIRECTION_NAMES.get(direction, str(direction))


def connect_to_device(ip_address: str) -> tuple:
    """
    Connect to device via TCP.

    Returns:
        Tuple of (data_source, config_interface) or (None, None) on failure.
    """
    try:
        # Parse the address
        if '://' not in ip_address:
            ip_address = f'tcp://{ip_address}'

        parts = urlparse(ip_address)
        address = parts.hostname
        port = parts.port if parts.port is not None else DEFAULT_TCP_PORT

        if address is None:
            print(f"Error: Invalid IP address format.")
            return None, None

        print(f"Connecting to {address}:{port}...")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((address, port))
        s.settimeout(None)

        data_source = SocketDataSource(s)
        config_interface = DeviceInterface(data_source)

        print("Connected successfully.")
        return data_source, config_interface

    except Exception as e:
        print(f"Error connecting to device: {e}")
        return None, None


def query_config(config_interface: DeviceInterface, config_type) -> object:
    """Query a configuration value from the device."""
    config_interface.get_config(ConfigurationSource.ACTIVE, config_type.GetType())
    resp = config_interface.wait_for_message(ConfigResponseMessage.MESSAGE_TYPE)

    if resp is None:
        return None
    if resp.response != Response.OK:
        return None

    return resp.config_object


def apply_config(config_interface: DeviceInterface, config_object, save: bool = False) -> bool:
    """Apply a configuration value to the device."""
    config_interface.set_config(config_object, save=save)
    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)

    if not isinstance(resp, CommandResponseMessage):
        return False
    if resp.response != Response.OK:
        print(f"Error: {resp.response}")
        return False

    return True


def save_all_config(config_interface: DeviceInterface) -> bool:
    """Save all configuration to persistent storage."""
    config_interface.send_save(SaveAction.SAVE)
    resp = config_interface.wait_for_message(CommandResponseMessage.MESSAGE_TYPE)

    if not isinstance(resp, CommandResponseMessage):
        return False
    if resp.response != Response.OK:
        print(f"Error saving: {resp.response}")
        return False

    return True


def prompt_float(prompt: str, current_value: float) -> float:
    """Prompt for a float value, showing current value as default."""
    while True:
        response = input(f"{prompt} [{current_value:.3f}]: ").strip()
        if response == '':
            return current_value
        try:
            return float(response)
        except ValueError:
            print("Please enter a valid number.")


def prompt_direction(prompt: str, current_value: Direction, valid_options: list = None) -> Direction:
    """Prompt for a direction value."""
    if valid_options is None:
        valid_options = list(DIRECTION_OPTIONS.keys())

    current_name = get_direction_name(current_value)
    options_str = ', '.join(valid_options)

    while True:
        response = input(f"{prompt} ({options_str}) [{current_name}]: ").strip().lower()
        if response == '':
            return current_value
        if response in DIRECTION_OPTIONS and response in valid_options:
            return DIRECTION_OPTIONS[response]
        print(f"Please enter one of: {options_str}")


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt for a yes/no response."""
    default_str = "Y/n" if default else "y/N"
    while True:
        response = input(f"{prompt} [{default_str}]: ").strip().lower()
        if response == '':
            return default
        if response in ('y', 'yes'):
            return True
        if response in ('n', 'no'):
            return False
        print("Please enter y or n.")


def print_section(title: str):
    """Print a section header."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_lever_arm(name: str, config):
    """Print lever arm values."""
    print(f"  {name}: X={config.x:.3f}m, Y={config.y:.3f}m, Z={config.z:.3f}m")


def print_orientation(config):
    """Print orientation values."""
    x_name = get_direction_name(config.x_direction)
    z_name = get_direction_name(config.z_direction)
    print(f"  Orientation: X-axis={x_name}, Z-axis={z_name}")


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format='%(message)s',
        stream=sys.stdout
    )

    print()
    print("=" * 60)
    print("  Point One Device Configuration Wizard")
    print("=" * 60)
    print()

    # Get device IP
    ip_address = input("Enter device IP address [192.168.0.1]: ").strip()
    if ip_address == '':
        ip_address = "192.168.0.1"

    # Connect to device
    data_source, config_interface = connect_to_device(ip_address)
    if config_interface is None:
        sys.exit(1)

    try:
        # Query current values
        print()
        print("Querying current configuration...")

        device_lever_arm = query_config(config_interface, DeviceLeverArmConfig)
        gnss_lever_arm = query_config(config_interface, GNSSLeverArmConfig)
        orientation = query_config(config_interface, DeviceCourseOrientationConfig)

        if device_lever_arm is None or gnss_lever_arm is None or orientation is None:
            print("Error: Failed to query current configuration.")
            sys.exit(1)

        # Display current values
        print_section("Current Configuration")
        print_lever_arm("IMU Lever Arm", device_lever_arm)
        print_lever_arm("GPS Lever Arm", gnss_lever_arm)
        print_orientation(orientation)

        # Track what changed
        changes = []

        # IMU Lever Arm
        print_section("IMU to Body Lever Arm (meters)")
        print("Enter the offset from the vehicle body origin to the IMU.")
        print("  +X = Forward, +Y = Left, +Z = Up")
        print()

        new_device_x = prompt_float("  X (forward)", device_lever_arm.x)
        new_device_y = prompt_float("  Y (left)", device_lever_arm.y)
        new_device_z = prompt_float("  Z (up)", device_lever_arm.z)

        if (new_device_x != device_lever_arm.x or
            new_device_y != device_lever_arm.y or
            new_device_z != device_lever_arm.z):
            new_device_lever_arm = DeviceLeverArmConfig(new_device_x, new_device_y, new_device_z)
            changes.append(('IMU Lever Arm', new_device_lever_arm))

        # GPS Lever Arm
        print_section("GPS Antenna to Body Lever Arm (meters)")
        print("Enter the offset from the vehicle body origin to the GPS antenna.")
        print("  +X = Forward, +Y = Left, +Z = Up")
        print()

        new_gnss_x = prompt_float("  X (forward)", gnss_lever_arm.x)
        new_gnss_y = prompt_float("  Y (left)", gnss_lever_arm.y)
        new_gnss_z = prompt_float("  Z (up)", gnss_lever_arm.z)

        if (new_gnss_x != gnss_lever_arm.x or
            new_gnss_y != gnss_lever_arm.y or
            new_gnss_z != gnss_lever_arm.z):
            new_gnss_lever_arm = GNSSLeverArmConfig(new_gnss_x, new_gnss_y, new_gnss_z)
            changes.append(('GPS Lever Arm', new_gnss_lever_arm))

        # Orientation
        print_section("Device Orientation")
        print("Specify how the device is mounted relative to the vehicle body.")
        print("  Vehicle body: +X = Forward, +Y = Left, +Z = Up")
        print()

        new_z_dir = prompt_direction(
            "  Device Z-axis points",
            orientation.z_direction,
            ['up', 'down', 'forward', 'backward', 'left', 'right']
        )
        new_x_dir = prompt_direction(
            "  Device X-axis points",
            orientation.x_direction,
            ['forward', 'backward', 'left', 'right', 'up', 'down']
        )

        if new_z_dir != orientation.z_direction or new_x_dir != orientation.x_direction:
            new_orientation = DeviceCourseOrientationConfig(new_x_dir, new_z_dir)
            changes.append(('Orientation', new_orientation))

        # Summary and confirmation
        if not changes:
            print()
            print("No changes made.")
            sys.exit(0)

        print_section("Summary of Changes")
        for name, config in changes:
            if isinstance(config, DeviceCourseOrientationConfig):
                x_name = get_direction_name(config.x_direction)
                z_name = get_direction_name(config.z_direction)
                print(f"  {name}: X-axis={x_name}, Z-axis={z_name}")
            else:
                print(f"  {name}: X={config.x:.3f}m, Y={config.y:.3f}m, Z={config.z:.3f}m")

        print()
        if not prompt_yes_no("Apply these changes?", default=True):
            print("Changes cancelled.")
            sys.exit(0)

        # Apply changes
        print()
        print("Applying changes...")
        for name, config in changes:
            print(f"  Setting {name}...", end=' ')
            if apply_config(config_interface, config):
                print("OK")
            else:
                print("FAILED")
                sys.exit(1)

        # Save to persistent storage
        print()
        if prompt_yes_no("Save changes to persistent storage?", default=True):
            print("Saving configuration...", end=' ')
            if save_all_config(config_interface):
                print("OK")
            else:
                print("FAILED")
                sys.exit(1)
        else:
            print("Changes applied but NOT saved. They will be lost on reboot.")

        print()
        print("Configuration complete!")

    finally:
        data_source.stop()


if __name__ == "__main__":
    main()
