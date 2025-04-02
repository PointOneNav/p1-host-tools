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

'''
This script is for TTFF testing, and the underlying process and hardware setup is described in this document:
https://docs.google.com/document/d/1kaCncm1S-A__UzS-jhh6DKekcIpC4QQg/edit?usp=sharing&ouid=116244641276191759860&rtpof=true&sd=true

The relevant fusion_engine_parameters.sh file used for this testing can be found here:
https://drive.google.com/file/d/12vRCAVdmoqSphmf5QdZk_nNNnPrl-W-_/view?usp=drive_link
'''

SSH_USERNAME = "pointone"
SSH_KEY_PATH = "/home/pointone/.ssh/id_rsa"
CSV_FILENAME = "/home/pointone/ttff.csv"
NUM_TESTS = 700

cmd_binary_path="/home/pointone/hidusb-relay-cmd"
rf_relay_number = 1
pi_relay_number = 2
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

def power_on_device(power_rc):
    power_rc.send_cmd(on_state=True)
    time.sleep(1)
    power_rc.send_cmd(on_state=False)

def power_cycle_device(pi_rc):
    pi_rc.send_cmd(on_state=True)
    time.sleep(10)
    pi_rc.send_cmd(on_state=False)
    time.sleep(1)
    power_on_device(pi_rc)

def is_network_available():
    try:
        subprocess.check_output(["ping", "-c", "1", "192.168.1.134"]) # Using ping as a simple check
        return True
    except subprocess.CalledProcessError:
        return False

def run_tests():
    test_start_time = time.time()

    times = []
    offset = 0
    for i in range(NUM_TESTS):
        print("---------------------------------------")

        logger.info(f"%s: Starting test %d" % (strftime("%a, %d %b %Y %X +0000", gmtime()), i+1))
        print("STARTING TEST", i+1)


        offset %= 30
        rf_rc = RelayController(rf_relay_number, cmd_binary_path=cmd_binary_path)
        power_rc = RelayController(power_relay_number, cmd_binary_path=cmd_binary_path)
        pi_rc = RelayController(power_relay_number, cmd_binary_path=cmd_binary_path)

        # Make sure that device can be reached.
        if not is_network_available():
            time_to_wait = 5
            print(f"Cannot ping device. Will try again in %d seconds." % time_to_wait)
            time.sleep(time_to_wait)


        # Set up SSH automation tool.
        pkey = paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f'Attempting to connect to SSH service for host {hostname}')

        # Connect via SSH.
        # NOTE: The following code block involves logic that was more relevant to the previous
        # TTFF setup, which involved shutting off and turning back on the whole DUT, rather than
        # just the receiver. The `except` statement below hasn't found to be called in the
        # current version of this script.
        retries = 0
        while True:
            try:
                ssh_client.connect(hostname=hostname, username=SSH_USERNAME, pkey=pkey)
                break
            except Exception as e:
                print("Failed to connect to host %s: %s. Going to reattempt." % (hostname, str(e)))
                if retries >= 2:
                    print("Failed to connect. Going to power cycle device.")
                    power_cycle_device(pi_rc)
                time.sleep(15)
                retries += 1

        # Check for successful connection.
        transport = ssh_client.get_transport()
        if transport is None or not transport.is_active():
            print('Failed to connect to host.')
            sys.exit(1)

        print("SSH connection successful")

        # Set up TCP connection. Device under test is set up to run FE service at startup.
        port = 30200
        response_timeout_sec = 30
        ip_address = socket.gethostbyname(hostname)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(response_timeout_sec)
        try:
            sock.connect((ip_address, port))
        except Exception as e:
            print("TCP connection unsuccessful. Power cycling device and starting test over.")
            power_cycle_device(pi_rc)
            time.sleep(10)
            continue

        print("TCP connection successful")

        # Create data source.
        data_source = SocketDataSource(sock)
        encoder = FusionEngineEncoder()

        ######## 1. DISCONNECT ANTENNA ########
        logger.info("%s: Disconnecting antenna" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        rf_rc.send_cmd(on_state=True)

        print("Antenna off")

        ######## 2. ISSUE COLD START COMMAND ########
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

                # NOTE: currenty, we don't check for the corner case where the solution type was _already_ invalid
                # before the cold start.
                if not reader.solution_valid:
                    logger.info("%s: Solution invalid. Continuing. " % strftime("%a, %d %b %Y %X +0000", gmtime()))
                    break

            except Exception:
                print("Error when reading from data source.")
                sys.exit(1)

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()
        # Wait for receiver to apply cold start; a wait of 3 seconds was found to be a suffient.
        time.sleep(3)

        ######## 4. SHUT DOWN ########
        # Stop FE service.
        print("Stopping FE service")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"sudo systemctl stop p1_fusion_engine.service")
        # Wait for exit status to ensure that command finished executing.
        exit_status = _stdout.channel.recv_exit_status()
        # NOTE: In the future, we'd ideally use a `kill -9` command to ensure that the process is stopped. Further,
        # we should check these exit status codes.
        time.sleep(5)
        # Remove cache files.
        print("Removing cache files")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"rm -rf /home/pointone/p1_fusion_engine/cache/fusion_engine")
        exit_status = _stdout.channel.recv_exit_status()

        logger.info("%s: Powering off reciever board." % strftime("%a, %d %b %Y %X +0000", gmtime()))
        power_rc.send_cmd(on_state=False)


        ######## 5. WAIT FOR DISCHARGE ########
        time_to_wait_after_poweroff = 10
        print("Waiting for %s seconds after power off." % time_to_wait_after_poweroff)
        time.sleep(time_to_wait_after_poweroff)

        ######## 6. RECONNECT ANTENNA ########
        logger.info("%s: Reconnecting antenna" % strftime("%a, %d %b %Y %X +0000", gmtime()))
        rf_rc.send_cmd(on_state=False)
        print("Antenna on.")

        ######## 7. RECONNECT POWER AND START TIMER ########
        # Insert sleep command in between OFF and ON command to relay to simulate power button.
        print("Sending power on signal to device after offset of %d seconds." % offset)
        logger.info("%s: Sending power on signal to device after offset of %d seconds" % (strftime("%a, %d %b %Y %X +0000", gmtime()), offset))
        time.sleep(offset)
        offset += 1

        logger.info("%s: Sending power ON command to device." % strftime("%a, %d %b %Y %X +0000", gmtime()))
        power_rc.send_cmd(on_state=True)
        start_time = time.time()

        # Wait after power on for a few seconds to turn on the p1_fusion_engine service. Found that 2 seconds
        # is minimum that will work consistently. Otherwise, the teseo config fixup script will fail to run.
        #
        # NOTE: in the future, we should modify this script so that it doesn't run the teseo config fixup
        # script, as the fixup script may have unintended impacts on the Teseo's startup.
        time.sleep(2)

        # Restart fusion engine service.
        print("Starting Fusion Engine service.")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"sudo systemctl start p1_fusion_engine.service")
        # Wait for exit status to ensure that command finished executing.
        exit_status = _stdout.channel.recv_exit_status()

        # Wait a few seconds before attempting to connect to output port. Found that 5 seconds is the minimum
        # that will allow the connection to work consistently.
        time_to_wait = 5
        print("Waiting %s seconds before connecting to output port." % time_to_wait)
        time.sleep(time_to_wait)

        print("Reconnecting to output port.")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(response_timeout_sec)
        try:
            sock.connect((ip_address, port))
        except Exception as e:
            print("TCP connection unsuccessful. Power cycling device and starting test over.")
            power_cycle_device(pi_rc)
            continue

        print("TCP connection successful")
        data_source = SocketDataSource(sock)

        # Get current log ID from device.
        stdin, stdout, stderr = ssh_client.exec_command(
                    "echo $(basename $(ls -l /logs/current_log | awk -F'-> ' '{print $2}'))")
        log_id = stdout.read().decode()
        logger.info("%s: Current log ID: %s" % (strftime("%a, %d %b %Y %X +0000", gmtime()), log_id))

        ######## 8. STOP TIMER AT FIRST FIX ########
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

        ######## 9. RECORD RESULTS ########
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

    print("Time to run", NUM_TESTS, "tests:", time.time() - test_start_time, "seconds")


if __name__ == "__main__":
    run_tests()
