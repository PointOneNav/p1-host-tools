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
    if input_path == '-':
        return sys.stdin.buffer
    elif input_path.endswith('.p1bin'):
        _logger.info(f"Reading raw data from p1bin {options.p1bin_type}.")
        return P1BinFileStream(input_path, options.p1bin_type, ignore_index=options.ignore_index,
                               show_read_progress=True)
    else:
        return open(input_path, 'rb')


def _create_framers(options, return_bytes=False):
    rtcm = RTCMFramer() if 'rtcm' in options.format else None
    fe = FusionEngineDecoder(max_payload_len_bytes=16536, return_offset=True,
                             return_bytes=return_bytes) if 'fe' in options.format else None
    nmea = NMEAFramer(return_offset=True) if 'nmea' in options.format else None
    return rtcm, fe, nmea


def _get_index_path(input_path, options):
    if len(options.format) < len(FORMAT_STRS):
        postfix = '_' + '_'.join(sorted(options.format)) + '_index.csv'
    else:
        postfix = '_index.csv'
    return get_output_file_path(input_path, postfix, output_dir=options.output_dir, prefix=options.prefix)


def _open_output_files(input_path, options, text_nmea=False):
    """!
    @brief Open output files for extraction.

    @return An `output_map` dict keyed by protocol name.
    """
    write_to_stdout = options.prefix == '-'
    output_map = {}

    if write_to_stdout:
        # Only one format may be written to stdout - error if multiple are requested.
        if len(options.format) > 1:
            _logger.error('Only one data type may be written to stdout.')
            sys.exit(1)

        # Map the single requested format to stdout. NMEA is text when the framer produces strings, RTCM and FE are
        # always binary.
        for fmt in ('nmea', 'rtcm', 'fe'):
            if fmt in options.format:
                output_map[fmt] = sys.stdout if (fmt == 'nmea' and text_nmea) else sys.stdout.buffer
                break
    else:
        # Open a dedicated output file for each requested protocol.
        if 'nmea' in options.format:
            output_map['nmea'] = open(
                get_output_file_path(input_path, '.nmea', output_dir=options.output_dir, prefix=options.prefix),
                'wt' if text_nmea else 'wb')
        if 'rtcm' in options.format:
            # When splitting by base station, start with file index 0. New files are opened as the base ID changes.
            suffix = '_0.rtcm3' if options.split_rtcm_base_id else '.rtcm3'
            output_map['rtcm'] = open(
                get_output_file_path(input_path, suffix, output_dir=options.output_dir, prefix=options.prefix), 'wb')
        if 'fe' in options.format:
            output_map['fe'] = open(
                get_output_file_path(input_path, '.p1log', output_dir=options.output_dir, prefix=options.prefix), 'wb')

    return output_map


def _stream_and_index(input_path, in_fd, options,
                      rtcm_framer, fe_framer, nmea_framer,
                      skip_bytes, bytes_to_process, file_size,
                      output_map, index_path):
    """
    @brief Core read loop: parse messages, write index CSV, and optionally extract to output files.

    Pass `output_map=None` for index-only (no extraction). `file_size=0` means stdin (progress shown as raw bytes).

    @return Returns a tuple: `(index, total_bytes_read)`.
    """
    extract = output_map is not None
    index = []
    total_bytes_read = 0
    current_base_id = -1
    rtcm_file_idx = 0

    # Open the index file if requested.
    timestamp_fd = None
    if index_path is not None:
        timestamp_fd = open(index_path, 'wt')
        timestamp_fd.write('Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n')

    start_time = datetime.now()
    next_update_time = 0

    # Status print helper function.
    def _print_status(elapsed_sec):
        if file_size == 0:
            _logger.info('Processed %d bytes. [elapsed=%.1f sec, rate=%.1f MB/s]' %
                         (total_bytes_read, elapsed_sec, total_bytes_read / elapsed_sec / 1e6))
        else:
            _logger.info('Processed %d/%d bytes (%.1f%%). [elapsed=%.1f sec, rate=%.1f MB/s]' %
                         (total_bytes_read, bytes_to_process,
                          100.0 * float(total_bytes_read) / bytes_to_process,
                          elapsed_sec, total_bytes_read / elapsed_sec / 1e6))

    # Read all incoming data until EOF or Ctrl-C.
    try:
        while True:
            # Print a progress update every 5 seconds.
            elapsed_sec = (datetime.now() - start_time).total_seconds()
            if elapsed_sec > next_update_time:
                _print_status(elapsed_sec)
                next_update_time = elapsed_sec + 5

            if total_bytes_read >= bytes_to_process:
                break

            data = in_fd.read(READ_SIZE)
            if not data:
                break

            total_bytes_read += len(data)

            # Parse all three protocols from the current chunk. Each entry is a
            # (protocol, id, stream_offset, size, p1_time) tuple.
            #
            # Note that we pass the entire chunk to each framer in sequence, not one byte at a time, so the framers may
            # output messages out of order.
            entries = []
            if rtcm_framer is not None:
                for msg in rtcm_framer.on_data(data, return_size=True, return_offset=True, return_bytes=extract):
                    message_id = msg["message"].message_id
                    offset_bytes = msg["offset"]
                    size_bytes = msg["size"]
                    entries.append(('rtcm', message_id, skip_bytes + offset_bytes, size_bytes, ''))

                    if extract:
                        raw_data = msg['bytes']

                        # If splitting by base station ID, open a new output file each time the base changes.
                        if options.split_rtcm_base_id and is_msm_id(message_id):
                            # Base station ID is encoded at bit offset 36, length 12 bits.
                            base_id = ((raw_data[4] & 0xF) << 8) + raw_data[5]
                            if base_id != current_base_id:
                                if current_base_id != -1:
                                    output_map['rtcm'].close()
                                    rtcm_file_idx += 1
                                    output_map['rtcm'] = open(get_output_file_path(
                                        input_path, f'_{rtcm_file_idx}.rtcm3',
                                        output_dir=options.output_dir, prefix=options.prefix), 'wb')
                                _logger.info(f"Writing for base station id: {base_id}")
                                current_base_id = base_id

                        output_map['rtcm'].write(raw_data)

            if fe_framer is not None:
                for result in fe_framer.on_data(data):
                    # The framer returns raw bytes as a third element only when constructed with return_bytes=True.
                    if extract:
                        header, payload, raw_data, offset_bytes = result
                    else:
                        header, payload, offset_bytes = result
                        raw_data = None

                    message_id = int(header.message_type)
                    size_bytes = header.get_message_size()
                    p1_time = payload.get_p1_time() if isinstance(payload, MessagePayload) else None
                    entries.append(('fe', message_id, skip_bytes + offset_bytes, size_bytes,
                                    '%.3f' % float(p1_time) if p1_time is not None else ''))

                    if extract:
                        output_map['fe'].write(raw_data)

            if nmea_framer is not None:
                for msg in nmea_framer.on_data(data):
                    # Note: NMEA messages are strings, not binary.
                    raw_data = msg[0]
                    message_id = raw_data.split(',')[0][1:]
                    offset_bytes = msg[1]
                    size_bytes = len(raw_data)
                    entries.append(('nmea', message_id, skip_bytes + offset_bytes, size_bytes, ''))

                    if extract:
                        output_map['nmea'].write(raw_data)

            # Accumulate index entries, dropping the P1 time field which is only written to the CSV.
            index.extend(e[:4] for e in entries)

            # Write entries to the index CSV sorted by byte offset within this chunk.
            if timestamp_fd is not None:
                for entry in sorted(entries, key=lambda e: e[2]):
                    timestamp_fd.write(f'{",".join([str(elem) for elem in entry])}\n')
    except (BrokenPipeError, KeyboardInterrupt):
        # User hit Ctrl-C - done processing.
        pass

    # Close the index file.
    if timestamp_fd is not None:
        # Write the EOF sentinel so future loads can verify the index covers the full byte range.
        timestamp_fd.write(f'{EOF_FORMAT},0,{bytes_to_process},0,\n')
        timestamp_fd.close()

    # Print final status after the loop exits.
    elapsed_sec = (datetime.now() - start_time).total_seconds()
    if elapsed_sec > 0:
        _print_status(elapsed_sec)

    return sorted(index, key=lambda e: e[2]), total_bytes_read


def index_messages(input_path, options):
    in_fd = get_fd(input_path, options)
    skip_bytes = options.skip_bytes

    # Determine the range of bytes to process.
    in_fd.seek(0, io.SEEK_END)
    file_size = in_fd.tell()
    in_fd.seek(skip_bytes, 0)

    bytes_to_process = file_size - skip_bytes
    if options.bytes_to_process is not None and options.bytes_to_process < bytes_to_process:
        bytes_to_process = options.bytes_to_process

    # Determine the path to the index file. If the file exists already, read it and return. If not, generate it.
    index_file_full = get_output_file_path(input_path, '_index.csv', output_dir=options.output_dir,
                                           prefix=options.prefix)
    index_file = _get_index_path(input_path, options)

    if not options.ignore_index:
        # Try to reuse a previously generated index if one exists and covers the same byte range.
        # When processing a subset of formats, also try the full-format index as a fallback.
        for index_file_to_load in {index_file, index_file_full}:
            if os.path.exists(index_file_to_load):
                index = load_index(index_file_to_load)

                # The index file should always end with an EOF marker. A missing marker means the file
                # was incomplete, likely due to an error during a previous indexing run.
                eof_index = index[-1] if index else None
                if eof_index is None or eof_index[0] != EOF_FORMAT:
                    _logger.warning(f'Index file "{index_file_to_load}" missing EOF entry, skipping load.')
                elif eof_index[2] != bytes_to_process:
                    _logger.info(
                        f'Index file "{index_file_to_load}" was generated for different input data, skipping load.')
                else:
                    _logger.info(f'Using existing index "{index_file_to_load}".')
                    return index, file_size

    # No usable existing index found; generate a new one by parsing the input file.
    _logger.info(f"Generating index file {index_file}.")
    rtcm_framer, fe_framer, nmea_framer = _create_framers(options)

    return _stream_and_index(input_path=input_path, in_fd=in_fd, options=options,
                             rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                             skip_bytes=skip_bytes, bytes_to_process=bytes_to_process, file_size=file_size,
                             output_map=None, index_path=index_file)


def load_index(index_file):
    indexes = []
    with open(index_file, 'r') as index_fd:
        # Skip the header line.
        index_fd.readline()
        for line in index_fd.readlines():
            fields = line.split(',')
            indexes.append(
                (fields[0], fields[1], int(fields[2]), int(fields[3])))

    # Sort by offset to handle any out-of-order entries caused by framer latency during data dropouts.
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
    # Open output files for each requested protocol.
    output_map = _open_output_files(input_path, options, text_nmea=False)
    current_base_id = -1
    rtcm_file_idx = 0

    # Seek to each message's offset and copy its bytes to the appropriate output file.
    in_fd = get_fd(input_path, options)
    for index in indexes:
        if index[0] in output_map:
            in_fd.seek(index[2], io.SEEK_SET)
            data = in_fd.read(index[3])

            # If splitting by base station, open a new file each time the base station ID changes.
            if options.split_rtcm_base_id and index[0] == 'rtcm' and is_msm_id(int(index[1])):
                # Base station ID is encoded at bit offset 36, length 12 bits.
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


def separate_and_index(input_path, options):
    # Open the input file (or stdin).
    in_fd = get_fd(input_path, options)
    read_from_stdin = in_fd is sys.stdin.buffer
    write_to_stdout = options.prefix == '-'

    # Determine byte range to process. For stdin the file size is unknown, so process all incoming data unless the user
    # specifies --bytes-to-process.
    skip_bytes = options.skip_bytes
    if read_from_stdin:
        file_size = 0
        bytes_to_process = options.bytes_to_process if options.bytes_to_process is not None else sys.maxsize
        try:
            in_fd.read(skip_bytes)
        except (BrokenPipeError, KeyboardInterrupt):
            return [], 0
    else:
        in_fd.seek(0, io.SEEK_END)
        file_size = in_fd.tell()
        in_fd.seek(skip_bytes, 0)
        bytes_to_process = file_size - skip_bytes
        if options.bytes_to_process is not None and options.bytes_to_process < bytes_to_process:
            bytes_to_process = options.bytes_to_process

    # Open output files for extraction if requested.
    output_map = _open_output_files(input_path, options, text_nmea=True) if options.extract else None

    # Open an index CSV only when writing output to disk, skip it when streaming to stdout.
    index_path = None if write_to_stdout else _get_index_path(input_path, options)

    rtcm_framer, fe_framer, nmea_framer = _create_framers(options, return_bytes=options.extract)

    # Run the streaming read loop until stopped or reaching EOF.
    index, total_bytes_read = _stream_and_index(
        input_path=input_path, in_fd=in_fd, options=options,
        rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
        skip_bytes=skip_bytes, bytes_to_process=bytes_to_process, file_size=file_size,
        output_map=output_map, index_path=index_path)

    return index, total_bytes_read


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
                    "file, or to the log directory if reading from a log. When reading from stdin, defaults to the "
                    "current working directory.")
parser.add_argument('-p', '--prefix', type=str,
                    help="Use the specified prefix for the output file: <prefix>.p1log, <prefix>.nmea, etc. Otherwise, "
                         "use the filename of the input data file. Set to '-' to write to stdout. If not specified and "
                         "reading from stdin, output will be written to stdout.")
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
parser.add_argument('log', nargs='?', default='-',
                    help="The log to be read. May be one of:\n"
                    "- The path to a binary log file\n"
                    "- The path to a FusionEngine log directory\n"
                    "- A pattern matching a FusionEngine log directory under the specified base directory "
                    "(see find_fusion_engine_log() and --log-base-dir)\n"
                    "- '-' or omit to read from stdin")


def raw_analysis(options):
    # If we're reading from stdin, we have some different behaviors below:
    # - If the user does not specify --prefix so we cannot set an output filename, we will write output to stdout
    # - If the user does specify --prefix but doesn't set --output-dir, we'll write to CWD
    # - When writing to stdout, we'll redirect logger prints to stderr
    read_from_stdin = options.log == '-'
    if read_from_stdin:
        if options.prefix is None:
            options.prefix = '-'

        if options.output_dir is None:
            options.output_dir = os.getcwd()

    write_to_stdout = options.prefix == '-'

    # When writing to stdout, we cannot split RTCM data into multiple files (there is only one stdout).
    if write_to_stdout:
        options.split_rtcm_base_id = False

    # Configure logging.
    if write_to_stdout:
        logging_stream = sys.stderr
    else:
        logging_stream = sys.stdout

    logger = logging.getLogger('point_one')
    if options.verbose >= 1:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(name)s:%(lineno)d - %(message)s',
                            stream=logging_stream)
        if options.verbose == 1:
            logger.setLevel(logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=logging_stream)

    # Locate the input file and set the output directory.
    if read_from_stdin:
        input_path = options.log
        output_dir = options.output_dir
        log_id = None
    else:
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
    else:
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

    # If reading from stdin, we can't preemptively index the data. Build the index as we go.
    if read_from_stdin:
        if options.extract:
            logger.info(f"Output stored in '{output_dir}'.")
        index, file_size_bytes = separate_and_index(input_path, options)
        if options.check_gaps:
            find_gaps(index)
    # If reading from a file, index the file and then perform the requested operation.
    else:
        logger.info(f"Output stored in '{output_dir}'.")
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
