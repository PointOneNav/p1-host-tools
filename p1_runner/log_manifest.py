import copy
import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Tuple

from gpstime import LEAPDATA, gpstime

from .device_type import DeviceType


class LogManifest(object):
    # Note: The filename misspelling is intentional.
    MANIFEST_FILENAME = 'maniphest.json'

    def __init__(self):
        self.guid = None
        self.prev_guid = None

        self.log_sequence_num = None
        self.creation_time = None
        self.creation_gps_time = None

        self.device_id = None
        self.device_type = None
        self.device_version = None
        self.system_config_path = None
        self.sw_version = None

        self.channels = []

    def to_json(self, pretty=True):
        data = copy.deepcopy(self.__dict__)

        if isinstance(data['creation_time'], datetime):
            data['creation_time'] = data['creation_time'].timestamp()

        if data['creation_gps_time'] is None and self.creation_time is not None:
            data['creation_gps_time'] = gpstime.fromdatetime(self.creation_time).gps()
        elif data['creation_gps_time'] is not None:
            if isinstance(data['creation_gps_time'], gpstime):
                data['creation_gps_time'] = data['creation_gps_time'].gps()
            elif isinstance(data['creation_gps_time'], datetime):
                data['creation_gps_time'] = gpstime.fromdatetime(self.creation_time).gps()

        if isinstance(data['device_type'], DeviceType):
            data['device_type'] = data['device_type'].name

        if pretty:
            return json.dumps(data, sort_keys=True, indent=4)
        else:
            return json.dumps(data)

    @classmethod
    def from_json(cls, contents):
        if isinstance(contents, str):
            data = json.loads(contents)
        else:
            data = contents

        result = LogManifest()
        result.guid = data.get('guid', None)
        result.prev_guid = data.get('prev_guid', None)

        result.log_sequence_num = data.get('log_sequence_num', None)

        result.creation_time = data.get('creation_time', None)
        if result.creation_time is not None:
            result.creation_time = datetime.fromtimestamp(result.creation_time, tz=timezone.utc)

        result.creation_gps_time = data.get('creation_gps_time', None)
        if result.creation_gps_time is not None:
            result.creation_gps_time = gpstime.fromgps(result.creation_gps_time)
        elif result.creation_time is not None:
            result.creation_gps_time = gpstime.fromdatetime(result.creation_time)

        result.device_id = data.get('device_id', None)
        result.device_type = DeviceType.from_string(data.get('device_type', None))

        result.device_version = data.get('device_version', None)
        result.system_config_path = data.get('system_config_path', None)
        result.sw_version = data.get('sw_version', None)

        result.channels = data.get('channels', [])

        return result

    def to_file(self, path, pretty=True):
        with open(path, 'w') as f:
            f.write(self.to_json(pretty=pretty))

    @classmethod
    def from_file(cls, path):
        stat = os.stat(path)
        if stat.st_size == 0:
            raise IOError("Manifest file '%s' is empty." % path)

        with open(path, 'r') as f:
            contents = json.load(f)
            return cls.from_json(contents)

    @classmethod
    def update_items_in_file(cls, path, items: Iterable[Tuple[str, Any]]):
        loaded = cls.from_file(path)
        for key, value in items:
            vars(loaded)[key] = value
        loaded.to_file(path)
