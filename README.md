# Point One Host Tools <!-- omit from toc -->
Tools for interfacing with Point One FusionEngine devices from a host computer.

This set of Python applications provides command-line tools for control, configuration, and data collection from Point One devices.

For a graphical application for interfacing Point One devices, use the [Point One Desktop](https://pointonenav.com/docs/) application.
This application and additional documentation on Point One devices, protocols, and software can be found at https://pointonenav.com/docs/.

## Table of Contents <!-- omit from toc -->
<!-- toc -->
- [Setup / Installation](#setup--installation)
  - [Windows Executables](#windows-executables)
  - [Python Setup](#python-setup)
    - [Using A Python Virtual Environment (venv)](#using-a-python-virtual-environment-venv)
- [Applications](#applications)
  - [`p1_runner` - Log Data And Receive GNSS Corrections In Real Time](#p1_runner---log-data-and-receive-gnss-corrections-in-real-time)
    - [Basic Usage](#basic-usage)
    - [Sending GNSS Corrections](#sending-gnss-corrections)
  - [`config_tool` - Read/Write Device Configuration](#config_tool---readwrite-device-configuration)
    - [Basic Usage](#basic-usage-1)
    - [Saving Changes](#saving-changes)
  - [`device_bridge` - Connect Two Devices Through The Host Computer](#device_bridge---connect-two-devices-through-the-host-computer)

<!-- tocstop -->

# Setup / Installation

## Windows Executables

For Windows users, pre-compiled Windows binaries can be downloaded as part of the
[GitHub releases](https://github.com/PointOneNav/p1-host-tools/releases). Windows users do not need to download or install Python.

## Python Setup

This repo is written in Python version 3. It includes a pip `requirements.txt` file, which details the dependencies needed to run the applications.

To get started, you can simply add the requirements to your system Python installation:

```sh
pip3 install -r requirements.txt
python3 config_tool.py
```

However, we strongly encourage the use of a Python virtual environment for managing dependencies (see below).

### Using A Python Virtual Environment (venv)

We strongly encourage the use of a Python virtual environment to avoid conflicts between the requirements for these tools with those of other
Python applications on your computer.

1. Create a new virtual environment.
   ```sh
   cd p1_runner
   python3 -m venv venv
   ```
   - You only need to do this once, unless you want to delete the virtual environment and recreate it.
2. Activate the virtual environment.

   Linux/Mac:
   ```sh
   source venv/bin/activate
   ```

   Windows:
   ```sh
   venv\Scripts\activate.bat
   ```
3. Install the latest requirements.
   ```sh
   pip install -r requirements.txt
   ```
   - You should redo this step each time `p1-host-tools` is updated to make sure you have the latest dependencies.
4. Run the applications.
   ```sh
   python config_tool.py
   ```

# Applications

## `p1_runner` - Log Data And Receive GNSS Corrections In Real Time

The `p1_runner` application connects to a device over serial in order to provide GNSS corrections data and log the device output.

`p1_runner` includes a built-in NTRIP client and support for receiving GNSS corrections from Point One's Polaris corrections network.

The following sections cover the most common use case. See `runner.py --help` for more detailed usage information and examples.

### Basic Usage

1. Connect a USB cable from the Point One device to your host computer.
2. If used, activate the Python virtual environment as described in [Setup / Installation](#setup--installation).
3. Run p1_runner to connect the device.

   Linux: `python3 bin/runner.py`

   Windows: `python bin/runner.py`

This will attempt to detect the appropriate serial port for the device. If the selected port is not correct, use the
`--device-port` argument to specify the correct port.

As it runs, the Python client application records all sensor measurements and generated output from the device into a log directory.
By default, logs are stored in `~logs` on Linux or `%HOME%\Documents\logs` (i.e., My Documents) on Windows.

### Sending GNSS Corrections

For precision applications, you must provide GNSS RTK corrections data. The Python client can be configured to connect to
Point One's Polaris corrections service or to an NTRIP server to receive corrections and relay them to the device.

To enable Polaris corrections, use the `--polaris` argument, providing an NTRIP password assigned by Point One. In addition, you
must specify an ID that uniquely identifies the device. There cannot be two concurrent connections to the Point One Polaris NTRIP
service with the same username and password, doing so will lead to undefined behavior. The username may only contain letters, numbers,
dashes, or underscores and can be at most 32 characters. For example:

```sh
$ python3 bin/runner.py --device-id my-device --polaris abcd1234
```

Contact Point One for a Polaris username and password.

To use another NTRIP service, use the `--ntrip` argument, specifying URL, mountpoint, and optionally username and password. For example:

```sh
$ python3 bin/runner.py --ntrip http://corrections.com:2101,my_mountpoint,my_username,my_password
```

## `config_tool` - Read/Write Device Configuration

`config_tool.py` can be used to query and update the setting and stored data on a Point One device.

The following sections cover the most common use case. See `config_tool.py --help` for more detailed usage information and examples.

### Basic Usage

1. Connect a USB cable from the Point One device to your host computer.
2. If used, activate the Python virtual environment as described in [Setup / Installation](#setup--installation).
3. Run config_tool to connect the device.

   Linux: `python3 bin/config_tool.py COMMAND [OPTIONS...]`
   
   Windows: `python bin/config_tool.py COMMAND [OPTIONS...]`


For example, to enable FusionEngine PoseMessages and disable NMEA GGA messages on UART1 run the following:

```sh
python3 bin/config_tool.py apply uart1 message_rate fusion_engine posemessage on
python3 bin/config_tool.py apply uart1 message_rate nmea gga off
python3 bin/config_tool.py save
```

### Saving Changes

Parameter changes take effect immediately after issuing an apply command:

```sh
python3 bin/config_tool.py apply ...
```

Applied settings are not saved to persistent storage automatically, and will be reset after a power cycle.
To save settings to persistent storage, issue a save command:

```sh
python3 bin/config_tool.py save
```

## `device_bridge` - Connect Two Devices Through The Host Computer

`device_bridge.py` is used to bridge two serial devices, forwarding the output from each device to the input of the other device.
This is most common if:
- One device is acting as an RTK base station for another device
- One device is a heading sensor (i.e., heading secondary device), sending
  heading measurements to a navigation engine (i.e., heading primary device)

For example, to connect device A on `/dev/ttyUSB0` to device B on `/dev/ttyUSB3`:

```sh
python3 bin/device_bridge.py /dev/ttyUSB0 /dev/ttyUSB3
```

See `device_bridge.py --help` for more detailed usage information and examples.
