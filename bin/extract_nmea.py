#!/usr/bin/env python3

import os
import re
import sys

from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, find_log_file

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser
from p1_runner.nmea_framer import NMEAFramer

READ_SIZE = 1024 * 1024


def main():
    # Parse arguments.
    parser = ArgumentParser(usage='%(prog)s [OPTIONS]...',
                            description="Extract NMEA data from a mixed binary file.")

    parser.add_argument('--log-base-dir', metavar='DIR', default=DEFAULT_LOG_BASE_DIR,
                        help="The base directory containing FusionEngine logs to be searched if a log pattern is"
                             "specified.")

    parser.add_argument('-o', '--output',
                        help="The path to an output file to be written. If omitted, defaults to INPUT.nmea in the "
                             "input file's parent directory. Relative paths are interpreted with respect to the input "
                             "file's parent directory. Set to - to write to stdout.")

    parser.add_argument('-p', '--pattern',
                        help="If set, only output NMEA messages matching the specified regex pattern.")

    parser.add_argument('-q', '--quiet',
                        help="If set, do not print progress details.")

    parser.add_argument('log',
                        help="The path to the input file to be read. May be one of:\n"
                             "- The path to a file containing NMEA data\n"
                             "- The path to a FusionEngine log directory\n"
                             "- A pattern matching a FusionEngine log directory under the specified base directory "
                             "(see find_fusion_engine_log() and --log-base-dir)")

    options = parser.parse_args()

    # Configure logging.
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    logger = logging.getLogger('point_one.extract_nmea')

    # Locate the input file.
    try:
        input_path, output_dir, log_id = find_log_file(options.log, candidate_files='input.raw',
                                                       return_output_dir=True, return_log_id=True,
                                                       log_base_dir=options.log_base_dir)

        if log_id is None:
            logger.info('Loading %s.' % os.path.basename(input_path))
        else:
            logger.info('Loading %s from log %s.' % (os.path.basename(input_path), log_id))
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    # Open the output file.
    file_prefix = os.path.splitext(os.path.basename(input_path))[0]
    if options.output == '-':
        out_file = sys.stdout
    else:
        if options.output is None:
            output_path = os.path.join(output_dir, file_prefix + '.nmea')
        elif os.path.isabs(options.output):
            output_path = options.output
        else:
            output_path = os.path.join(output_dir, options.output)

        logger.info('Saving output to %s.' % output_path)
        out_file = open(output_path, 'wt')

    # Open the input file and look for NMEA data.
    with open(input_path, 'rb') as in_file:
        input_size = os.path.getsize(input_path)

        if options.pattern is not None:
            pattern = re.compile(options.pattern)
        else:
            pattern = None

        framer = NMEAFramer()
        bytes_read = 0
        nmea_bytes_read = 0
        last_print = 0
        while True:
            data = in_file.read(READ_SIZE)
            if len(data) == 0:
                break

            bytes_read += len(data)

            messages = framer.on_data(data)

            # If a regex pattern was specified, discard any messages that don't match the pattern.
            if pattern is not None:
                messages = [m for m in messages if re.search(pattern, m)]

            nmea_bytes_read += sum(len(s) for s in messages)
            out_file.write(''.join(messages))

            if not options.quiet:
                bytes_since_print = bytes_read - last_print
                if bytes_since_print > 10e6:
                    logger.info('%d bytes processed (%.1f%%). [nmea=%d B]' %
                                (bytes_read, bytes_read * 100.0 / input_size, nmea_bytes_read))
                    last_print = bytes_read

    if not options.quiet:
        logger.info('Finished:')
        logger.info('  Processed: %d B' % bytes_read)
        logger.info('  NMEA extracted: %d B' % nmea_bytes_read)


if __name__ == "__main__":
    main()
