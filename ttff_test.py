import csv
import os
import socket
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

solution_valid = True

class PoseMessageReader():
    def __init__(self):
        self.solution_valid = True

    def on_pose_message(self, header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
        self.solution_valid = pose.solution_type != SolutionType.Invalid

def power_on_device(power_rc):
    power_rc.send_cmd(on_state=True)
    time.sleep(1)
    power_rc.send_cmd(on_state=False)

# def on_pose_message(header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
#     solution_valid = pose.solution_type != SolutionType.Invalid

if __name__ == "__main__":
    test_start_time = time.time()
    num_tests = 700
    times = []
    for i in range(num_tests):
        print("---------------------------------------")
        print("STARTING TEST", i+1)
        # Make sure that fusion_engine process is running.

        hostname = "192.168.1.134"

        # Set up SSH automation tool.
        pkey = paramiko.RSAKey.from_private_key_file(SSH_KEY_PATH)
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        print(f'Attempting to connect to host {hostname}')

        # Connect via SSH.
        try:
            ssh_client.connect(hostname=hostname, username=SSH_USERNAME, pkey=pkey)
        except Exception as e:
            print("Failed to connect to host %s: %s" % (hostname, str(e)))
            sys.exit(1)

        # Check for successful connection.
        transport = ssh_client.get_transport()
        if transport is None or not transport.is_active():
            print('Failed to connect to host.')
            sys.exit(1)


        # Set up TCP connection.
        port = 30200
        response_timeout_sec = 100
        ip_address = socket.gethostbyname(hostname)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((ip_address, port))
        sock.settimeout(response_timeout_sec)



        # #### 1. DISCONNECT ANTENNA ####
        cmd_binary_path="/home/pointone/hidusb-relay-cmd"
        rf_relay_number = 1
        rf_rc = RelayController(rf_relay_number, cmd_binary_path=cmd_binary_path)

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
        _stdin, _stdout, _stderr = ssh_client.exec_command(f"systemctl poweroff -i")
        # Wait for exit status to ensure that tar command finished executing.
        exit_status = _stdout.channel.recv_exit_status()

        #### 5. WAIT FOR DISCHARGE ####
        print("Waiting")
        time.sleep(15)

        #### 6. RECONNECT ANTENNA ####
        rf_rc.send_cmd(on_state=False)
        print("Antenna on")

        #### 7. RECONNECT POWER AND START TIMER ####
        power_relay_number = 2
        power_rc = RelayController(power_relay_number, cmd_binary_path=cmd_binary_path)

        # # Insert sleep command in between OFF and ON command to relay to simulate power button.
        print("Sending power on signal")
        power_on_device(power_rc=power_rc)
        start_time = time.time()

        #### 8. STOP TIMER AT FIRST FIX ####
        # Continue reading until we get a pose message with a valid solution.
        # ip_address = socket.gethostbyname(hostname)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.connect((ip_address, port))
        except Exception:
            time_to_wait = 10
            print("Failed to connect. Going to attempt to power on and try again in 10 seconds.")
            time.sleep(10)
            power_on_device(power_rc=power_rc)
            start_time = time.time()
            sock.connect((ip_address, port))

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


        #### 9. RECORD RESULTS ####
        end_time = time.time()
        total_time_elapsed = end_time - start_time
        times.append(total_time_elapsed)
        print("Total time elapsed:", total_time_elapsed)
        with open(CSV_FILENAME, 'a') as f:
            csv_writer = csv.writer(f)
            csv_writer.writerow([round(total_time_elapsed, 4)])


    print("TIMES (SECONDS):")
    print(times)
    print("Average time:", np.mean(times), "seconds")
    print("Max time:", max(times), "seconds")
    print("Min time:", min(times), "seconds")

    print("Time to run", num_tests, "tests:", time.time() - test_start_time, "seconds")