import json
import logging
import re
from datetime import datetime
from typing import List, NamedTuple, Optional, Tuple
from zipfile import ZipFile

from fusion_engine_client.messages import DataType, DataVersion, ImportDataMessage, PlatformStorageDataMessage, Response, VersionInfoMessage

logger = logging.getLogger('point_one.exported_data')


def is_export_valid(save_file: str) -> bool:
    try:
        with ZipFile(save_file, 'r') as export_zip:
            return True
    except:
        return False


def create_exported_data(save_file: str, version: VersionInfoMessage):
    meta_data = {
        'timestamp': datetime.utcnow().isoformat(),
        'device_version': {
            'firmware': version.fw_version_str,
            'fusion_engine': version.engine_version_str,
            'os': version.os_version_str,
            'gnss_reciever': version.rx_version_str
        }
    }
    logger.info('Creating new export archive: "%s"', save_file)
    with ZipFile(save_file, 'w') as export_zip:
        export_zip.writestr('meta.json', json.dumps(
            meta_data, indent=2, sort_keys=True))


def add_to_exported_data(save_file: str, exported_data: PlatformStorageDataMessage):
    file_prefix = f'{exported_data.data_type.name}-{exported_data.data_version.major}_{exported_data.data_version.minor}-{exported_data.response.name}'
    with ZipFile(save_file, 'a') as export_zip:
        logger.info('Creating binary save of exported %s',
                    exported_data.data_type.name)
        export_zip.writestr(file_prefix + '.bin', exported_data.data)


class _ExportInfo(NamedTuple):
    type: DataType
    version: DataVersion
    validity: Response
    encoding: str
    file: str


def _find_match(exported_data: List[_ExportInfo], type: DataType, encoding: str) -> Optional[_ExportInfo]:
    for info in exported_data:
        if info.type == type and info.encoding == encoding:
            return info
    return None


def load_saved_data(save_file: str, types: List[DataType]) -> List[Tuple[ImportDataMessage, Response]]:
    imports = []
    file_info = []
    with ZipFile(save_file, 'r') as export_zip:
        exported_files = export_zip.namelist()
        logger.debug('Archive files: %s', exported_files)
        name_re = re.compile(
            r'([A-Z_]+)-([0-9]+)_([0-9]+)-([A-Z_]+)\.([a-z]+)')
        for exported_file in exported_files:
            m = name_re.match(exported_file)
            if m is not None:
                file_info.append(_ExportInfo(
                    DataType[m.group(1)],
                    DataVersion(
                        int(m.group(2)),
                        int(m.group(3))),
                    Response[m.group(4)],
                    m.group(5),
                    exported_file
                ))

        for file_data_type in types:
            data = None
            export_info = None
            if data is None:
                export_info = _find_match(
                    file_info, file_data_type, 'bin')
                if export_info is not None:
                    if export_info.validity == Response.NO_DATA_STORED:
                        data = bytes()
                    else:
                        logger.info(
                            'Importing binary save of exported data: %s', export_info.file)
                        # This is a binary file, but `rb` appears to not supported.
                        with export_zip.open(export_info.file, 'r') as fd:
                            data = fd.read()

            if data is not None and export_info is not None:
                imports.append((ImportDataMessage(
                    data_type=export_info.type, data_version=export_info.version, data=data), export_info.validity))

    return imports
