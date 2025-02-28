import csv
import os
import socket
import subprocess
import sys
import time

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
power_relay_number = 2
hostname = "192.168.1.134"


class PoseMessageReader():
    def __init__(self):
        self.solution_valid = True

    def on_pose_message(self, header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
        self.solution_valid = pose.solution_type != SolutionType.Invalid
        print(self.solution_valid)

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



# def on_pose_message(header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
#     solution_valid = pose.solution_type != SolutionType.Invalid

if __name__ == "__main__":
    test_start_time = time.time()
    num_tests = 700
    times = []
    for i in range(num_tests):
        print("---------------------------------------")
        print("STARTING TEST", i+1)

        rf_rc = RelayController(rf_relay_number, cmd_binary_path=cmd_binary_path)
        power_rc = RelayController(power_relay_number, cmd_binary_path=cmd_binary_path)

        # Make sure that fusion_engine process is running.

        if not is_network_available():
            print("Cannot ping device. Attempting to power on device.")
            power_on_device(power_rc)
            time.sleep(30)


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
                power_cycle_device(power_rc)
                time.sleep(15)

        # Check for successful connection.
        transport = ssh_client.get_transport()
        if transport is None or not transport.is_active():
            print('Failed to connect to host.')
            sys.exit(1)

        print("SSH connection successful")

        # _stdin, _stdout, _stderr = ssh_client.exec_command(f"rm -rf /home/pointone/p1_fusion_engine/cache/fusion_engine")

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
            power_cycle_device(power_rc)
            continue

        print("TCP connection successful")


        # #### 1. DISCONNECT ANTENNA ####
        rf_rc.send_cmd(on_state=True)

        print("Antenna off")

        # time.sleep(1)

        # rc.send_cmd(on_state=False)
        # print("second command sent")

        #### 2. ISSUE COLD START COMMAND ####
        print("Issuing cold start")
        message = ResetRequest(reset_mask=ResetRequest.COLD_START)

        # Create data source.
        data_source = SocketDataSource(sock)

        encoder = FusionEngineEncoder()
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
                    print("Solution invalid. Cold start succesfully applied.")
                    break

            except Exception:
                print("Error when reading from data source.")
                sys.exit(1)

        sock.shutdown(socket.SHUT_RDWR)
        sock.close()

        #### 4. SHUT DOWN ####
        print("Sending shutdown command")

        _stdin, _stdout, _stderr = ssh_client.exec_command(f"rm -rf /home/pointone/p1_fusion_engine/cache/fusion_engine")
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"systemctl poweroff -i")
        # Wait for exit status to ensure that tar command finished executing.
        # exit_status = _stdout.channel.recv_exit_status()



        #### 5. WAIT FOR DISCHARGE ####
        time_to_wait_after_poweroff = 15
        print("Waiting for %s seconds after power off" % time_to_wait_after_poweroff)
        time.sleep(time_to_wait_after_poweroff)

        #### 6. RECONNECT ANTENNA ####
        rf_rc.send_cmd(on_state=False)
        print("Antenna on")

        #### 7. RECONNECT POWER AND START TIMER ####
        # # Insert sleep command in between OFF and ON command to relay to simulate power button.
        print("Sending power on signal")
        power_on_device(power_rc=power_rc)
        start_time = time.time()

        # time.sleep(10)

        #### 8. STOP TIMER AT FIRST FIX ####
        # Continue reading until we get a pose message with a valid solution.
        # ip_address = socket.gethostbyname(hostname)
        # Don't attempt to connect via TCP if network is not ready.
        # print("Checking for network availability after power on.")
        # counter = 0
        # success = False
        # while time.time() - start_time < 15:
        #     # counter += 1
        #     # time.sleep(1)

        #     # if counter > 3:
        #     #     break
        #     if is_network_available():
        #         success = True
        #         break

        #     print("Pinging network again.")

        # if not success:
        #     print("Network not found in time. Power cycling and restarting test.")
        #     power_cycle_device(power_rc)
        #     continue

        # print("Network available after power on. Continuing.")

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(response_timeout_sec)
        socket_connection_trials = 0
        max_socket_connection_trials = 500
        time_start_socket_connection_attempts = time.time()
        connection_success = False
        print("Attempting to make socket connection.")
        while time.time() - time_start_socket_connection_attempts < 60:
            try:
                sock.connect((ip_address, port))
                connection_success = True
                break
            except Exception:
                socket_connection_trials += 1
                # print("Failed to connect after power on. Going to try again")
                # continue
                # time_to_wait = 10
                # print("Failed to connect. Going to attempt to power on and try again in", time_to_wait, "seconds.")
                # time.sleep(time_to_wait)
                # power_on_device(power_rc=power_rc)
                # start_time = time.time()
                # try:
                #     sock.connect((ip_address, port))
                # except Exception:
                #     print("Failed to connect again. Going to power cycle and start test over.")
                #     power_cycle_device(power_rc)
                #     continue

        if not connection_success:
            print("Cannot make socket connection. Starting test over.")
            continue

        print("Time spent trying to connect to socket after power on (sec):", time.time() - time_start_socket_connection_attempts)

        data_source = SocketDataSource(sock)
        print("Waiting for first valid solution.")
        while True:
            try:
                data = data_source.read(1024)
                decoder.on_data(data)

                if reader.solution_valid:
                    print("Solution valid. System successfully navigating.")
                    break

            except Exception:
                print("Error when reading from data source.")
                sys.exit(1)

        end_time = time.time()

        #### 9. RECORD RESULTS ####
        total_time_elapsed = end_time - start_time
        time_since_cold_start_sent = end_time - time_cold_start_sent
        times.append(total_time_elapsed)
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