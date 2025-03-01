import csv
import os
import socket
import subprocess
import sys
import time
from time import gmtime, strftime

import logging
import numpy as np
import paramiko

from relay_controller import RelayController

from fusion_engine_client.messages import *
from fusion_engine_client.parsers import FusionEngineDecoder, FusionEngineEncoder

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner.data_source import SocketDataSource

SSH_USERNAME = "pointone"
SSH_KEY_PATH = "/home/pointone/.ssh/id_rsa"
CSV_FILENAME = "/home/pointone/ttff.csv"

cmd_binary_path="/home/pointone/hidusb-relay-cmd"
rf_relay_number = 1
# Associated with powering the AJ board, NOT the Pi connected to the AJ board.
power_relay_number = 3
hostname = "192.168.1.134"

logging.basicConfig(filename='ttff.log', level=logging.INFO, filemode='w', format='%(asctime)s - %(levelname)s - %(message)s')

logger = logging.getLogger('ttff_test')


class PoseMessageReader():
    def __init__(self):
        self.solution_valid = True

    def on_pose_message(self, header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
        self.solution_valid = pose.solution_type != SolutionType.Invalid and pose.solution_type != SolutionType.Integrate
        # print("Solution valid?", self.solution_valid)

def power_on_device(power_rc):
    power_rc.send_cmd(on_state=True)
    time.sleep(1)
    power_rc.send_cmd(on_state=False)

def power_cycle_device(power_rc):
    power_rc.send_cmd(on_state=True)
    time.sleep(10)
    power_rc.send_cmd(on_state=False)
    time.sleep(1)
    power_on_device(power_rc)

def is_network_available():
    try:
        subprocess.check_output(["ping", "-c", "1", "192.168.1.134"]) # Using ping as a simple check
        return True
    except subprocess.CalledProcessError:
        return False


if __name__ == "__main__":
    test_start_time = time.time()
    num_tests = 700
    times = []
    offset = 0
    for i in range(num_tests):
        print("---------------------------------------")

        logger.info(f"%s: Starting test %d" % (strftime("%a, %d %b %Y %X +0000", gmtime()), i+1))
        print("STARTING TEST", i+1)

        # time.sleep(30)

        offset %= 30
        rf_rc = RelayController(rf_relay_number, cmd_binary_path=cmd_binary_path)
        power_rc = RelayController(power_relay_number, cmd_binary_path=cmd_binary_path)

        # Make sure that fusion_engine process is running.

        if not is_network_available():
            print("Cannot ping device. Attempting to power on device.")
            # power_on_device(power_rc)
            # time.sleep(30)


        # Set up SSH automation tool.
        pkey = paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f'Attempting to connect to SSH service for host {hostname}')

        # Connect via SSH.
        while True:
            try:
                ssh_client.connect(hostname=hostname, username=SSH_USERNAME, pkey=pkey)
                break
            except Exception as e:
                print("Failed to connect to host %s: %s. Going to attempt a power cycle." % (hostname, str(e)))
                # power_cycle_device(power_rc)
                time.sleep(15)

        # Check for successful connection.
        transport = ssh_client.get_transport()
        if transport is None or not transport.is_active():
            print('Failed to connect to host.')
            sys.exit(1)

        print("SSH connection successful")
        print("Removing cache files")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"rm -rf /home/pointone/p1_fusion_engine/cache/fusion_engine")
        exit_status = _stdout.channel.recv_exit_status()

        # Set up TCP connection.
        port = 30200
        response_timeout_sec = 30
        ip_address = socket.gethostbyname(hostname)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(response_timeout_sec)
        try:
            sock.connect((ip_address, port))
        except Exception as e:
            print("TCP connection unsuccessful. Power cycling device and starting test over.")
            # power_cycle_device(power_rc)
            continue

        print("TCP connection successful")

        # Create data source.
        data_source = SocketDataSource(sock)

        encoder = FusionEngineEncoder()


        # #### 1. DISCONNECT ANTENNA ####
        logger.info("%s: Disconnecting antenna" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        rf_rc.send_cmd(on_state=True)
        # test_startup_time = time.time()

        print("Antenna off")

        # time.sleep(1)

        # rc.send_cmd(on_state=False)
        # print("second command sent")

        #### 2. ISSUE COLD START COMMAND ####
        logger.info("%s: Issuing cold start" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        message = ResetRequest(reset_mask=ResetRequest.COLD_START)


        encoded_data = encoder.encode_message(message)
        #### 3. WAIT FOR TESEO RECEIVER TO APPLY COLD START ####
        reader = PoseMessageReader()
        decoder = FusionEngineDecoder(max_payload_len_bytes=PoseMessage.calcsize(), warn_on_unrecognized=False, return_bytes=True)
        decoder.add_callback(PoseMessage.MESSAGE_TYPE, reader.on_pose_message)
        sock.send(encoded_data)
        print("Cold start command sent")
        time_cold_start_sent = time.time()
        while True:
            try:
                data = data_source.read(1024)
                decoder.on_data(data)

                if not reader.solution_valid:
                    logger.info("%s: Solution invalid. Continuing. " % strftime("%a, %d %b %Y %X +0000", gmtime()))
                    break

            except Exception:
                print("Error when reading from data source.")
                sys.exit(1)

        # time.sleep(10)

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        # Wait for receiver to apply cold start.
        time.sleep(3)

        #### 4. SHUT DOWN ####
        # Might want to stop FE service here, then wait a few seconds (3ish seconds) for it to actually shut down, then delete cache files
        print("Stopping FE service")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"sudo systemctl stop p1_fusion_engine.service")
        # Wait for exit status to ensure that command finished executing.
        exit_status = _stdout.channel.recv_exit_status()
        time.sleep(5)
        # Remove cache files.
        print("Removing cache files")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"rm -rf /home/pointone/p1_fusion_engine/cache/fusion_engine")
        exit_status = _stdout.channel.recv_exit_status()
        # for lnie in _stdout.readlines()


        logger.info("%s: Sending power OFF command to device." % strftime("%a, %d %b %Y %X +0000", gmtime()))
        power_rc.send_cmd(on_state=False)

        # _stdin, _stdout, _stderr = ssh_client.exec_command(f"systemctl poweroff -i")

        # Wait for exit status to ensure that command finished executing.
        # exit_status = _stdout.channel.recv_exit_status()


        #### 5. WAIT FOR DISCHARGE ####
        time_to_wait_after_poweroff = 10
        print("Waiting for %s seconds after power off" % time_to_wait_after_poweroff)
        time.sleep(time_to_wait_after_poweroff)


        #### 6. RECONNECT ANTENNA ####
        logger.info("%s: Reconnecting antenna" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        rf_rc.send_cmd(on_state=False)
        # TRY THIS
        # start_time = time.time()
        print("Antenna on")

        #### 7. RECONNECT POWER AND START TIMER ####
        # Insert sleep command in between OFF and ON command to relay to simulate power button.
        print("Sending power on signal to device after offset of", offset, "seconds")
        logger.info("%s: Sending power on signal to device after offset of %d seconds" % (strftime("%a, %d %b %Y %X +0000", gmtime()), offset))
        time.sleep(offset)
        offset += 1

        # power_on_device(power_rc=power_rc)
        logger.info("%s: Sending power ON command to device." % strftime("%a, %d %b %Y %X +0000", gmtime()))
        power_rc.send_cmd(on_state=True)
        start_time = time.time()

        # Wait after power on for a few seconds to turn on the p1_fusion_engine service. Found that 2 seconds
        # is minimum that will work consistently. Otherwise, the teseo config fixup script will fail to run.
        time.sleep(2)

        # Restart fusion engine service
        print("Starting fusion engine service")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"sudo systemctl start p1_fusion_engine.service")
        # Wait for exit status to ensure that command finished executing.
        exit_status = _stdout.channel.recv_exit_status()
        # print(output)
        # Wait a few seconds before attempting to connect to output port. Found that 5 seconds is the minimum
        # that will allow the connection to work consistently.
        time_to_wait = 5
        print("Waiting %s seconds before connecting to output port" % time_to_wait)
        time.sleep(time_to_wait)

        print("Reconnecting to output port.")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(response_timeout_sec)
        try:
            sock.connect((ip_address, port))
        except Exception as e:
            print("TCP connection unsuccessful. Power cycling device and starting test over.")
            # power_cycle_device(power_rc)
            continue

        print("TCP connection successful")
        data_source = SocketDataSource(sock)

        # Get current log ID from device.
        stdin, stdout, stderr = ssh_client.exec_command(
                    "echo $(basename $(ls -l /logs/current_log | awk -F'-> ' '{print $2}'))")
        log_id = stdout.read().decode()
        logger.info("%s: Current log ID: %s" % (strftime("%a, %d %b %Y %X +0000", gmtime()), log_id))

        logger.info("%s: Waiting for first valid solution" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        while True:
            try:
                data = data_source.read(1024)
                decoder.on_data(data)

                if reader.solution_valid:
                    logger.info("%s: Received first valid solution. System successfully navigating." % strftime("%a, %d %b %Y %X +0000", gmtime()))
                    break

            except Exception:
                print("Error when reading from data source.")
                sys.exit(1)

        end_time = time.time()

        #### 9. RECORD RESULTS ####
        total_time_elapsed = end_time - start_time
        time_since_cold_start_sent = end_time - time_cold_start_sent
        times.append(total_time_elapsed)
        logger.info("Time elapsed since power on: %d" % total_time_elapsed)
        logger.info("Time elapsed since cold start sent: %d" % time_since_cold_start_sent)
        print("Time elapsed since power on:", total_time_elapsed)
        print("Time elapsed since cold start sent:", time_since_cold_start_sent)
        with open(CSV_FILENAME, 'a') as f:
            csv_writer = csv.writer(f)
            csv_writer.writerow([round(total_time_elapsed, 4)])


    print("TIMES (SECONDS):")
    print(times)
    print("Average time:", np.mean(times), "seconds")
    print("Max time:", max(times), "seconds")
    print("Min time:", min(times), "seconds")

    print("Time to run", num_tests, "tests:", time.time() - test_start_time, "seconds")