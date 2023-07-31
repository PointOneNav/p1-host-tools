try:
    from contextlib import nullcontext
except ImportError:
    # nullcontext was added to contextlib in Python 3.7.
    from contextlib import AbstractContextManager

    class nullcontext(AbstractContextManager):
        def __init__(self, enter_result=None):
            self.enter_result = enter_result

        def __enter__(self):
            return self.enter_result

        def __exit__(self, *excinfo):
            pass

import logging
import os
import select
import signal
import subprocess
import threading

import psutil


class SeggerRTTCapture(threading.Thread):
    logger = logging.getLogger("point_one.rtt_client")

    DEFAULT_TELNET_PORT = 19021
    INTERNAL_TELNET_PORT = 21234

    def __init__(self, output_path, print_output=False, segger_dir=("/opt/SEGGER",),
                 force_kill_gdbserver=False, telnet_port=None):
        threading.Thread.__init__(self)

        self.rtt_path = self.find_segger_app("JLinkRTTClient", segger_dir)
        if self.rtt_path is None:
            self.logger.warning("SEGGER JLinkRTTClient application not found. Disabling RTT capture.")
            return
        else:
            self.logger.debug("Found RTT client: %s" % self.rtt_path)

        self.jlink_exe_path = self.find_segger_app("JLinkExe", segger_dir)
        if self.jlink_exe_path is None:
            self.logger.debug("JLinkExe not found.")
        else:
            self.logger.debug("Found JLinkExe client: %s" % self.jlink_exe_path)

        self.output_path = output_path
        self.print_output = print_output

        self.rtt_telnet_port = telnet_port
        self.force_kill_gdbserver = force_kill_gdbserver

        self.jlink_process = None

        self.shutdown_pending = threading.Event()

    def start(self):
        if self.is_alive():
            self.logger.warning("RTT client already running.")
            return
        elif self.rtt_path is not None:
            self.logger.debug("Starting RTT client.")
            self.shutdown_pending.clear()
            super().start()

    def stop(self):
        if self.is_alive():
            self.logger.debug("Shutting down RTT client.")
            self.shutdown_pending.set()

    def run(self):
        if self.output_path is None:
            out_file = nullcontext()
            if not self.print_output:
                return
        else:
            if os.path.exists(self.output_path):
                self.logger.warning("Overwriting existing RTT output file '%s'." % self.output_path)

            try:
                out_file = open(self.output_path, 'w')
                self.logger.debug("Saving RTT output to '%s'." % self.output_path)
                out_file.write("-- Recording log %s..." % os.path.basename(os.path.dirname(self.output_path)))
            except Exception as e:
                self.logger.error("Unable to open RTT output file '%s': %s" % (self.output_path, repr(e)))
                return

        # First, make sure there's a server available for the RTT client to connect to.
        try:
            telnet_port = self._run_server(force_kill_gdbserver=self.force_kill_gdbserver)
        except Exception as e:
            self.logger.error("Unable to establish JLink server connection: %s" % repr(e))
            out_file.close()
            return

        # Now, run the RTT client to capture output.
        with out_file:
            rtt = None
            connected = False
            while not self.shutdown_pending.is_set():
                # Under normal circumstances, RTT client should connect and start capturing right away.
                #
                # If gdbserver is not running, however, it may set attempting to connect indefinitely until the JTAG
                # eventually comes up (gdbserver is started):
                #   ###RTT Client: Connecting to J-Link RTT Server via localhost:19021 ...
                # In that case, we still want to be responsive to application shutdown requests.
                #
                # Separately, if the device is being reprogrammed, RTT can sometimes error out with:
                #   ###RTT Client ERROR: Connection refused - There already is an active connection.
                # For that, we want to retry the connection until it succeeds.
                if rtt is None:
                    try:
                        # Note that we set stdin to PIPE here so the RTT child process does _not_ receive signals sent
                        # to this program. That way, if the user sends a SIGINT (Ctrl-C) or similar, it doesn't get
                        # forwarded directly to RTT, and instead we can shut it down cleanly.
                        self.logger.debug("Starting RTT client.")
                        rtt = subprocess.Popen([self.rtt_path, '-RTTTelnetPort', str(telnet_port)],
                                               universal_newlines=True, bufsize=1,
                                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               stdin=subprocess.PIPE)
                        connected = False
                    except Exception as e:
                        # If we can't open RTT client at all (bad path, bad permissions, etc.), we will never be able
                        # to.
                        self.logger.error("Unable to run SEGGER RTT client: %s" % repr(e))
                        return

                try:
                    result = select.select([rtt.stdout], [], [], 0.25)
                    if len(result[0]) == 0:
                        continue

                    line = rtt.stdout.readline()

                    if line == '':
                        self.logger.debug("RTT client exited.")
                        rtt.wait()
                        rtt = None
                        connected = False
                        break
                    elif line.startswith("###RTT Client ERROR:"):
                        if 'Connection refused' in line:
                            self.logger.debug("RTT connection refused. Retrying in 2 seconds.")
                        else:
                            self.logger.error("Unexpected error from RTT client: %s" % line)

                        rtt.terminate()
                        rtt = None
                        self.shutdown_pending.wait(2.0)
                        continue
                    elif line.startswith("###RTT Client: Connected."):
                        self.logger.debug("RTT client connected successfully. Starting capture.")
                        connected = True
                        continue
                    elif connected:
                        if self.print_output:
                            self.logger.info(line.rstrip())
                        if self.output_path is not None:
                            out_file.write(line)
                            out_file.flush()
                except KeyboardInterrupt:
                    continue
                except UnicodeDecodeError as e:
                    self.logger.error("Error capturing RTT output: %s" % repr(e))
                    continue

            if rtt is not None:
                self.logger.debug("Stopping RTT client.")
                rtt.terminate()
                try:
                    rtt.wait(0.5)
                    result = rtt.stdout.read()
                    if self.print_output:
                        for line in result.rstrip().splitlines():
                            self.logger.info(line)
                    if self.output_path is not None:
                        out_file.write(rtt.stdout.read())
                except subprocess.TimeoutExpired:
                    self.logger.warning("Timed out waiting for RTT client to shutdown.")

            if self.jlink_process is not None:
                self.logger.debug("Stopping JLinkExe server.")
                self.jlink_process.terminate()
                try:
                    self.jlink_process.wait(0.5)
                except subprocess.TimeoutExpired:
                    self.logger.warning("Timed out waiting for JLinkExe server to shutdown.")

    def _run_server(self, force_kill_gdbserver=False, quiet=False):
        # JLinkRTTClient is a telnet client, and connects to a local server which, in turn, talks to the actual device
        # over JTAG. That server can be either of:
        # - JLinkGDBServer (gdbserver)
        # - JLinkExe (JTAG interactive terminal)
        #
        # If JLinkGDBServer is already running, we'll use that as the telnet server and assume the user intends to debug
        # the device with gdb. We'll issue a warning in case that isn't what they wanted.
        processes = [p for p in psutil.process_iter() if p.name() == 'JLinkGDBServer']
        gdbserver = processes[0] if len(processes) > 0 else None
        if gdbserver is not None:
            if force_kill_gdbserver:
                os.kill(gdbserver.pid, signal.SIGTERM)
            else:
                telnet_port = self.DEFAULT_TELNET_PORT
                self.logger.debug('JLinkGDBServer detected. Using as telnet server on port %d.' % telnet_port)

                if not quiet:
                    self.logger.warning("""\
Connecting JLink console to gdbserver. If you do not plan to debug the
device with gdb, please exit gdbserver and run again (or run with the
--rtt-force option to close gdbserver automatically).""")

                return telnet_port

        # If we get this far, they are not using gdb and just want to boot the device (or we just killed gdbserver
        # above). Run JLinkExe in the background and connect to that. We'll run it on an alternate telnet port just in
        # case they reopen JLinkGDBServer, but that's not a guarantee it'll work right if they do.
        #
        # If JLinkExe is already running, we'll assume it was run manually on the default telnet port, and we'll just
        # connect to it. If it's running on another port, the user can pass telnet_port=N to this class and it will be
        # forwarded to JLinkRTTClient.
        processes = [p for p in psutil.process_iter() if p.name() == 'JLinkExe']
        jlinkexe = processes[0] if len(processes) > 0 else None
        if jlinkexe is not None:
            telnet_port = self.rtt_telnet_port if self.rtt_telnet_port is not None else self.DEFAULT_TELNET_PORT
            self.logger.debug('JLinkExe already running. Using as telnet server on port %d.' % telnet_port)
        else:
            telnet_port = self.rtt_telnet_port if self.rtt_telnet_port is not None else self.INTERNAL_TELNET_PORT
            self.logger.debug('Running JLinkExe as telnet server on port %d.' % telnet_port)
            command = f"{self.jlink_exe_path} -Device STM32H743AI -If SWD -Speed 4000 -AutoConnect 1 " \
                      f"-RTTTelnetPort {telnet_port}"
            self.jlink_process = subprocess.Popen(command.split(" "), stdin=subprocess.PIPE,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return telnet_port

    @classmethod
    def find_segger_app(cls, name, parent_dir):
        if isinstance(parent_dir, (list, set, tuple)):
            parent_dirs = parent_dir
        else:
            parent_dirs = (parent_dir,)

        for parent_dir in parent_dirs:
            for root, dirs, files in os.walk(parent_dir, followlinks=True):
                if name in files:
                    return os.path.join(root, name)

        return None
