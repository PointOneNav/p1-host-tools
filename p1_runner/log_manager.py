import os
import queue
import struct
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import trace as logging
from .log_manifest import DeviceType, LogManifest


class LogManager(threading.Thread):
    logger = logging.getLogger('point_one.log_manager')

    PREV_GUID_PATTERN = 'prev_guid_%s.txt'
    SEQUENCE_NUMBER_FILE = 'sequence_num.txt'

    def __init__(
            self, device_id, device_type='UNKNOWN', logs_base_dir='/logs', files=None, log_extension='.raw',
            create_symlink=True, log_created_cmd=None, log_timestamps=True, directory_to_reuse: Optional[str] = None):
        super().__init__(name='log_manager')

        self.device_id = device_id
        self.device_type = device_type
        self.logs_base_dir = logs_base_dir
        self.create_symlink = create_symlink
        self.log_created_cmd = log_created_cmd
        self.data_filename = 'input' + log_extension
        self.directory_to_reuse = directory_to_reuse

        self.log_guid = None
        self.creation_time = None
        self.sequence_num = None
        self.log_dir = None
        self.log_timestamps = log_timestamps
        self.start_time = time.time()
        self.last_timestamp = time.time()

        if files is not None:
            self.files = list(files)
        else:
            self.files = []

        self.data_queue = queue.Queue()

    def get_log_directory(self):
        return self.log_dir

    def get_abs_file_path(self, relative_path):
        if self.log_dir is None:
            raise ValueError('Log manager not started. Cannot determine log directory.')
        else:
            return os.path.join(self.log_dir, relative_path)

    def create_log_dir(self):
        self.log_guid = str(uuid.uuid4()).replace('-', '')
        self.creation_time = datetime.now(tz=timezone.utc)
        if self.directory_to_reuse:
            self.log_dir = self.directory_to_reuse
            if not os.path.exists(self.log_dir):
                raise IOError("Log directory '%s' doesn't exists." % self.log_dir)
        else:
            self.log_dir = os.path.join(self.logs_base_dir, self.creation_time.strftime('%Y-%m-%d'), self.device_id,
                                        self.log_guid)
            if os.path.exists(self.log_dir):
                raise IOError("Log directory '%s' already exists." % self.log_dir)
            else:
                os.makedirs(self.log_dir)

        self.sequence_num = self._next_sequence_number()
        self.logger.info("Creating log for device '%s'. [log_num=%d, path='%s']" %
                         (self.device_id, self.sequence_num, self.log_dir))

        if self.create_symlink:
            symlink_path = os.path.join(self.logs_base_dir, 'current_log')

            create_link = True
            if os.path.islink(symlink_path):
                os.unlink(symlink_path)
            elif os.path.exists(symlink_path):
                # This is a special case for Windows symlinks created with `mklink /D`. We do not actually expect
                # current_log to be a _real_ directory ever, and if it was, trying to remove it with rmdir() would
                # fail if it was not empty.
                if sys.platform == 'win32':
                    os.rmdir(symlink_path)
                else:
                    self.logger.warning("Unable to delete existing '%s' contents. Cannot create symlink." %
                                        symlink_path)
                    create_link = False

            if create_link:
                os.symlink(os.path.relpath(self.log_dir, self.logs_base_dir), symlink_path, target_is_directory=True)

        self._create_manifest()

    def start(self):
        self.logger.debug('Starting log manager.')
        if self.log_dir is None:
            self.create_log_dir()
        super().start()

    def stop(self):
        if self.is_alive():
            self.logger.debug('Stopping log manager.')
            self.data_queue.put(None)

    def write(self, data):
        if not self.is_alive():
            return

        if isinstance(data, str):
            data = data.encode('utf-8')

        self.data_queue.put(data)

    def run(self):
        path = os.path.join(self.log_dir, self.data_filename)
        self.logger.debug("Opening bin file '%s'." % path)
        timestamp_file = None
        if self.log_timestamps:
            timestamp_path = os.path.join(self.log_dir, self.data_filename + '.timestamps')
            self.logger.debug("Opening timestamp file '%s'." % timestamp_path)
            timestamp_file = open(timestamp_path, 'wb')
        with open(path, 'wb') as bin_file:
            if self.log_created_cmd is not None:
                try:
                    subprocess.Popen(self.log_created_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     shell=True)
                except Exception as e:
                    self.logger.warning("Error running log created command: %s" % repr(e))

            while True:
                data = self.data_queue.get()
                if data is None:
                    break
                else:
                    size = len(data)
                    self.logger.trace('Writing %d bytes.' % size)
                    bin_file.write(data)
                    timestamp = time.time()
                    if timestamp_file and timestamp - self.last_timestamp > 0.001:
                        # This will rollover after about about 50 days.
                        milliseconds = int(round((timestamp - self.start_time) * 1000.)) % 2**32
                        # This will rollover after about about 26 hours of full rate 460800 baud data.
                        offset = bin_file.tell() % 2**32
                        data = struct.pack('II', milliseconds, offset)
                        timestamp_file.write(data)
                        self.last_timestamp = timestamp

        if timestamp_file is not None:
            timestamp_file.close()

        self.logger.info("Log data stored in '%s'." % self.log_dir)

    def update_manifest(self, items):
        if 'device_type' in items:
            self.device_type = items['device_type'] if items['device_type'] is not None else 'UNKNOWN'

        path = os.path.join(self.log_dir, LogManifest.MANIFEST_FILENAME)
        LogManifest.update_items_in_file(path, items)

    def _create_manifest(self):
        manifest = LogManifest()
        manifest.guid = self.log_guid
        manifest.prev_guid = self._get_prev_log()
        manifest.log_sequence_num = self.sequence_num
        manifest.creation_time = self.creation_time
        manifest.device_id = self.device_id
        manifest.device_type = self.device_type if self.device_type is not None else 'UNKNOWN'

        manifest.channels.append(self.data_filename)
        manifest.channels.extend(self.files)
        manifest.channels.sort()

        path = os.path.join(self.log_dir, LogManifest.MANIFEST_FILENAME)
        self.logger.debug("Creating manifest file '%s'." % path)
        manifest.to_file(path)

    def _next_sequence_number(self):
        path = os.path.join(self.logs_base_dir, self.SEQUENCE_NUMBER_FILE)

        try:
            with open(path, 'r') as f:
                prev_sequence_num = int(f.read())
        except:
            prev_sequence_num = 0

        sequence_num = prev_sequence_num + 1

        try:
            with open(path, 'w') as f:
                f.write('%d' % sequence_num)
        except Exception as e:
            self.logger.error("Unable to update log sequence number file '%s': %s" % (path, repr(e)))

        return sequence_num

    def _get_prev_log(self):
        path = os.path.join(self.logs_base_dir, self.PREV_GUID_PATTERN % self.device_id)

        try:
            with open(path, 'r') as f:
                prev_log_guid = f.read()
                if prev_log_guid == '':
                    prev_log_guid = None
        except:
            prev_log_guid = None

        try:
            with open(path, 'w') as f:
                f.write(self.log_guid)
        except Exception as e:
            self.logger.error("Unable to update prev GUID file '%s': %s" % (path, repr(e)))

        return prev_log_guid
