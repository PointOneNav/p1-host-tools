#!/usr/bin/env python3

import io
import os
import sys
from datetime import datetime

from construct import *
from fusion_engine_client.parsers.decoder import (FusionEngineDecoder,
                                                  MessagePayload)
from fusion_engine_client.utils.argument_parser import (ArgumentParser,
                                                        CSVAction,
                                                        ExtendedBooleanAction)
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, find_log_file

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.nmea_framer import NMEAFramer
from p1_runner.p1bin_reader import P1BinFileStream, P1BinType
from p1_runner.p1bin_type import find_matching_p1bin_types
from p1_runner.rtcm_framer import RTCMFramer

_logger = logging.getLogger('point_one.raw_analysis')

READ_SIZE = 1024

FORMAT_STRS = set(('fe', 'nmea', 'rtcm'))
EOF_FORMAT = 'eof'


def is_msm_id(msg_id):
    return msg_id == 1005 or msg_id == 1006 or (msg_id >= 1071 and msg_id <= 1227)


def get_output_file_path(input_path, postfix, output_dir=None, prefix=None):
    if output_dir is None:
        output_dir = os.path.dirname(input_path)
    if prefix is None:
        prefix = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, prefix + postfix)


def get_fd(input_path: str, options):
    if input_path.endswith('.p1bin'):
        _logger.info(f"Reading raw data from p1bin {options.p1bin_type}.")
        return P1BinFileStream(input_path, options.p1bin_type, ignore_index=options.ignore_index,
                               show_read_progress=True)
    else:
        return open(input_path, 'rb')


def index_messages(input_path, options):
    rtcm_framer = RTCMFramer() if 'rtcm' in options.format else None
    fe_framer = FusionEngineDecoder(
        max_payload_len_bytes=4096, return_offset=True) if 'fe' in options.format else None
    nmea_framer = NMEAFramer(
        return_offset=True) if 'nmea' in options.format else None

    in_fd = get_fd(input_path, options)
    skip_bytes = options.skip_bytes

    in_fd.seek(0, io.SEEK_END)
    file_size = in_fd.tell()
    in_fd.seek(skip_bytes, 0)

    bytes_to_process = file_size - skip_bytes
    if options.bytes_to_process is not None and options.bytes_to_process < bytes_to_process:
        bytes_to_process = options.bytes_to_process

    # File without prefix indicates all parsers used.
    index_file_full = get_output_file_path(
        input_path, '_index.csv', output_dir=options.output_dir, prefix=options.prefix)

    if len(options.format) < len(FORMAT_STRS):
        index_file = get_output_file_path(
            input_path, '_' + '_'.join(options.format) + '_index.csv', output_dir=options.output_dir,
            prefix=options.prefix)
    else:
        index_file = index_file_full

    if not options.ignore_index:
        # Try using the full index when processing a subset of formats.
        index_files_to_load = set([index_file, index_file_full])
        for index_file_to_load in index_files_to_load:
            if os.path.exists(index_file_to_load):
                index = load_index(index_file_to_load)

                # Check if index was generated for same datafile.
                #
                # The index file should always have an EOF marker. If it does not, we might have run into an error while
                # generating it.
                eof_index = index[-1] if len(index) > 0 else None
                if eof_index is None or eof_index[0] != EOF_FORMAT:
                    _logger.warning(
                        f'Index file "{index_file_to_load}" missing EOF entry, skipping load.')
                else:
                    if eof_index[2] != bytes_to_process:
                        _logger.info(
                            f'Index file "{index_file_to_load}" was generated for different input data, skipping load.')
                    else:
                        _logger.info(
                            f'Using existing index "{index_file_to_load}".')
                        return index, file_size

    _logger.info(f"Indexing raw input.")

    start_time = datetime.now()
    next_update_time = 0

    with open(index_file, 'w') as timestamp_fd:
        timestamp_fd.write(
            'Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n')
        while True:
            total_bytes_read = in_fd.tell() - skip_bytes
            elapsed_sec = (datetime.now() - start_time).total_seconds()
            if elapsed_sec > next_update_time:
                next_update_time = elapsed_sec + 5
                _logger.log(logging.INFO,
                            'Processed %d/%d bytes (%.1f%%). [elapsed=%.1f sec, rate=%.1f MB/s]' %
                            (total_bytes_read, bytes_to_process, 100.0 * float(total_bytes_read) / bytes_to_process,
                                elapsed_sec, total_bytes_read / elapsed_sec / 1e6))

            if total_bytes_read > bytes_to_process:
                break

            data = in_fd.read(READ_SIZE)

            if len(data) == 0:
                break

            entries = []
            if rtcm_framer is not None:
                for msg in rtcm_framer.on_data(data, return_size=True, return_offset=True):
                    entries.append(
                        ('rtcm', msg["message"].message_id, skip_bytes + msg["offset"], msg["size"], ''))
            if fe_framer is not None:
                for header, payload, offset_bytes in fe_framer.on_data(data):
                    p1_time = payload.get_p1_time() if isinstance(payload, MessagePayload) else None
                    entries.append(('fe', int(header.message_type), skip_bytes + offset_bytes,
                                    header.get_message_size(),
                                    '%.3f' % float(p1_time) if p1_time is not None else ''))
            if nmea_framer is not None:
                for msg in nmea_framer.on_data(data):
                    entries.append(('nmea', msg[0].split(
                        ',')[0][1:], skip_bytes + msg[1], len(msg[0]), ''))

            for entry in sorted(entries, key=lambda e: e[2]):
                timestamp_fd.write(
                    f'{",".join([str(elem) for elem in entry])}\n')

        timestamp_fd.write(f'{EOF_FORMAT},0,{bytes_to_process},0,\n')

    total_bytes_read = bytes_to_process
    _logger.log(logging.INFO,
                'Processed %d/%d bytes (%.1f%%). [elapsed=%.1f sec, rate=%.1f MB/s]' %
                (total_bytes_read, bytes_to_process, 100.0 * float(total_bytes_read) / bytes_to_process,
                    elapsed_sec, total_bytes_read / elapsed_sec / 1e6))

    return load_index(index_file), file_size


def load_index(index_file):
    indexes = []
    with open(index_file, 'r') as index_fd:
        index_fd.readline()
        for line in index_fd.readlines():
            fields = line.split(',')
            indexes.append(
                (fields[0], fields[1], int(fields[2]), int(fields[3])))

    # If the data has dropouts, the messages might not be in order due to how long the framers take to detect the error.
    indexes = sorted(indexes, key=lambda x: x[2])
    return indexes


def find_gaps(indexes):
    next_offset = 0
    has_gaps = False
    for index in indexes:
        if index[2] != next_offset and index[0] != EOF_FORMAT:
            has_gaps = True
            gap = index[2] - next_offset
            _logger.info(
                f"Data at offset {next_offset}B is not framed for {gap}B.")
        next_offset = index[2] + index[3]

    if not has_gaps:
        _logger.info(f"No gaps found.")


def generate_separated_logs(input_path, indexes, options):
    output_map = {}
    current_base_id = -1
    rtcm_file_idx = 0
    if 'nmea' in options.format:
        # Note need the write binary to avoid needing to decode the ascii in the for loop.
        output_map['nmea'] = open(get_output_file_path(input_path, '.nmea',
                                  output_dir=options.output_dir, prefix=options.prefix), 'wb')
    if 'rtcm' in options.format:
        suffix = '_0.rtcm3' if options.split_rtcm_base_id else '.rtcm3'
        output_map['rtcm'] = open(get_output_file_path(input_path, suffix,
                                  output_dir=options.output_dir, prefix=options.prefix), 'wb')
    if 'fe' in options.format:
        output_map['fe'] = open(get_output_file_path(input_path, '.p1log',
                                output_dir=options.output_dir, prefix=options.prefix), 'wb')

    in_fd = get_fd(input_path, options)
    for index in indexes:
        if index[0] in output_map:
            in_fd.seek(index[2], io.SEEK_SET)
            data = in_fd.read(index[3])
            if options.split_rtcm_base_id and index[0] == 'rtcm' and is_msm_id(int(index[1])):
                # offset 36 bits, length 12 bits.
                base_id = ((data[4] & 0xF) << 8) + data[5]
                if base_id != current_base_id:
                    if current_base_id != -1:
                        output_map['rtcm'].close()
                        rtcm_file_idx += 1
                        output_map['rtcm'] = open(get_output_file_path(
                            input_path, f'_{rtcm_file_idx}.rtcm3',
                            output_dir=options.output_dir, prefix=options.prefix), 'wb')
                    _logger.info(f"Writing for base station id: {base_id}")

                current_base_id = base_id
            output_map[index[0]].write(data)


parser = ArgumentParser(description="""\
Analyze contents of a input.raw or input.p1bin and create csv with offset and
length of each NMEA, RTCM, and FE message. Print out locations of data gaps.
""")
parser.add_argument('--log-base-dir', metavar='DIR', default=DEFAULT_LOG_BASE_DIR,
                    help="The base directory containing FusionEngine logs to be searched if a log pattern is"
                    "specified.")
parser.add_argument('-v', '--verbose', action='count', default=0,
                    help="Print verbose/trace debugging messages.")
parser.add_argument('-d', '--bytes-to-process', default=None, type=int,
                    help="If set, only process at most N bytes from the input file.")
parser.add_argument('-s', '--skip-bytes', default=0, type=int,
                    help="If set, only start analysis this many bytes into the data.")
parser.add_argument('-i', '--ignore-index', action='store_true',
                    help="If set, re-run index generation.")
parser.add_argument('-o', '--output-dir', type=str, metavar='DIR',
                    help="The directory where output will be stored. Defaults to the parent directory of the input"
                    "file, or to the log directory if reading from a log.")
parser.add_argument('-p', '--prefix', type=str,
                    help="Use the specified prefix for the output file: `<prefix>.p1log`. Otherwise, use the "
                    "filename of the input data file.")
parser.add_argument(
    '-t', '--p1bin-type', type=str, action='append',
    help="An optional list message types to analyse from a p1bin file. Defaults to 'EXTERNAL_UNFRAMED_GNSS'. Only used "
         "if the raw log is a *.p1bin file. May be specified multiple times (-m DEBUG -m EXTERNAL_UNFRAMED_GNSS), or "
         "as a comma-separated list (-m DEBUG,EXTERNAL_UNFRAMED_GNSS). All matches are case-insensitive.\n"
         "\n"
         "If a partial name is specified, the best match will be returned. Use the wildcard '*' to match multiple "
         "message types.\n"
         "\n"
         "Supported types:\n%s" % '\n'.join(['- %s' % c for c in P1BinType]))
parser.add_argument('-f', '--format', type=str, action=CSVAction,
                    help="An optional list of message formats to search for. May be specified "
                    "multiple times (-f nmea -f rtcm), or as a comma-separated list (-m nmea,rtcm). All matches are "
                    "case-insensitive.\n"
                    "\n"
                    "Supported types:\n%s" % '\n'.join(['- %s' % c for c in FORMAT_STRS]))
parser.add_argument('-e',
                    '--extract', action=ExtendedBooleanAction,
                    help="If set, separate the contents of each format type into their own files.")
parser.add_argument('--split-rtcm-base-id', action=ExtendedBooleanAction,
                    help="If set, separate the RTCM contents into separate files each time the base station changes. "
                         "The file names will end with '_N.rtcm' where N is the the count of base stations seen.")
parser.add_argument('--check-gaps', action=ExtendedBooleanAction, default=True,
                    help="If set, search for unframed bytes that do not belong to a complete message from any "
                         "protocol, indicating the existence of a gap in the data stream.")
parser.add_argument('log',
                    help="The log to be read. May be one of:\n"
                    "- The path to a binary log file\n"
                    "- The path to a FusionEngine log directory\n"
                    "- A pattern matching a FusionEngine log directory under the specified base directory "
                    "(see find_fusion_engine_log() and --log-base-dir)")


def raw_analysis(options):
    # Configure logging.
    logger = logging.getLogger('point_one')
    if options.verbose >= 1:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(name)s:%(lineno)d - %(message)s',
                            stream=sys.stdout)
        if options.verbose == 1:
            logger.setLevel(logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO,
                            format='%(message)s', stream=sys.stdout)

    # Locate the input file and set the output directory.
    try:
        input_path, output_dir, log_id = find_log_file(options.log, candidate_files=['input.raw', 'input.p1bin'],
                                                       return_output_dir=True, return_log_id=True,
                                                       log_base_dir=options.log_base_dir)

        if log_id is None:
            logger.info('Loading %s.' % os.path.basename(input_path))
        else:
            logger.info('Loading %s from log %s.' %
                        (os.path.basename(input_path), log_id))

    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    if options.format is not None:
        # If the user specified a set of formats, lookup their type values. Below, we will limit the processing to only
        # those format types.
        format = set(f.lower() for f in options.format)
        for f in format:
            if f not in FORMAT_STRS:
                logger.error(f'Invalid format "{f}".')
                sys.exit(1)
        options.format = format
        format_str = '_' + '_'.join(format)
    else:
        format_str = ''
        options.format = FORMAT_STRS
    logger.info(f"Processing {options.format}.")

    # Use the EXTERNAL_UNFRAMED_GNSS unless the user explicitly specified different P1BinType.
    if options.p1bin_type is not None:
        # Pattern match to any of:
        #   -t Type1
        #   -t Type1 -t Type2
        #   -t Type1,Type2
        #   -t Type1,Type2 -t Type3
        #   -t Type*
        try:
            options.p1bin_type = find_matching_p1bin_types(options.p1bin_type)
            if len(options.p1bin_type) == 0:
                # find_matching_message_types() will print an error.
                sys.exit(1)
        except ValueError as e:
            _logger.error(str(e))
            sys.exit(1)
    else:
        options.p1bin_type = [P1BinType.EXTERNAL_UNFRAMED_GNSS]

    logger.info(f"Output index stored in '{output_dir}'.")
    index, file_size_bytes = index_messages(input_path, options)

    if options.check_gaps:
        find_gaps(index)

    if options.extract:
        generate_separated_logs(input_path, index, options)

    _logger.info("")
    format_string = '| {:<10} | {:>10} | {:>10} |'
    _logger.info(format_string.format('Protocol', 'Messages', 'Bytes'))
    _logger.info(format_string.format('-' * 10, '-' * 10, '-' * 10))
    bytes_used = 0
    for format in sorted(options.format):
        message_length_bytes = [e[3] for e in index if e[0] == format]
        format_bytes = sum(message_length_bytes)
        bytes_used += format_bytes
        _logger.info(format_string.format(format, len(message_length_bytes), format_bytes))

    _logger.info("")
    _logger.info(f"File size: {file_size_bytes} B")
    processed_bytes = file_size_bytes - options.skip_bytes
    if options.skip_bytes > 0:
        _logger.info(f"File considered: {processed_bytes} B")
    _logger.info(f"Unused: {processed_bytes - bytes_used} B")


def extract_format(format):
    parser.description = f'Extract {format} contents of an input.raw or input.p1bin file.'
    parser.remove_argument('--format')
    parser.remove_argument('--extract')
    parser.remove_argument('--check-gaps')
    options = parser.parse_args()
    options.format = set((format,))
    options.extract = True
    options.check_gaps = False
    raw_analysis(options)


def main():
    options = parser.parse_args()
    raw_analysis(options)


if __name__ == "__main__":
    main()
