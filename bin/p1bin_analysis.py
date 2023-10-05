#!/usr/bin/env python3

from p1_runner.p1bin_type import find_matching_p1bin_types
from p1_runner.p1bin_reader import P1BinReader, P1BinType
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, find_log_file
from fusion_engine_client.utils.argument_parser import ArgumentParser
import os
import sys

from p1_runner import trace as logging

_logger = logging.getLogger('point_one.p1bin_analysis')


def main():
    parser = ArgumentParser(description="""\
Analyze contents of a .p1bin and extract data types to their own files.
""")

    parser.add_argument('-e', '--extract', action='store_true',
                        help="If set, separate the contents of each record type into their own files.")
    parser.add_argument(
        '-i', '--ignore-index', action='store_true',
        help="If set, do not load the .p1i index file corresponding with the .p1log data file. If specified and a .p1i "
        "file does not exist, do not generate one. Otherwise, a .p1i file will be created automatically to "
        "improve data read speed in the future.")
    parser.add_argument('--log-base-dir', metavar='DIR', default=DEFAULT_LOG_BASE_DIR,
                        help="The base directory containing FusionEngine logs to be searched if a log pattern is"
                             "specified.")
    parser.add_argument('-o', '--output', type=str, metavar='DIR',
                        help="The directory where output will be stored. Defaults to the parent directory of the input"
                             "file, or to the log directory if reading from a log.")
    parser.add_argument('-p', '--prefix', type=str,
                        help="Use the specified prefix for the output file: `<prefix>.p1log`. Otherwise, use the "
                             "filename of the input data file.")
    parser.add_argument(
        '-t', '--p1bin-type', type=str, action='append',
        help="An optional list of class names corresponding with the message types to be displayed. May be specified "
             "multiple times (-m DEBUG -m EXTERNAL_UNFRAMED_GNSS), or as a comma-separated list (-m "
             "DEBUG,EXTERNAL_UNFRAMED_GNSS). All matches are case-insensitive.\n"
             "\n"
             "If a partial name is specified, the best match will be returned. Use the wildcard '*' to match multiple "
             "message types.\n"
             "\n"
             "Supported types:\n%s" % '\n'.join(['- %s' % c for c in P1BinType]))
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages.")
    parser.add_argument('log',
                        help="The log to be read. May be one of:\n"
                             "- The path to a .p1bin file\n"
                             "- The path to a FusionEngine log directory\n"
                             "- A pattern matching a FusionEngine log directory under the specified base directory "
                             "(see find_fusion_engine_log() and --log-base-dir)")

    options = parser.parse_args()

    # Configure logging.
    logger = logging.getLogger('point_one')
    if options.verbose >= 1:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(name)s:%(lineno)d - %(message)s',
                            stream=sys.stdout)
        if options.verbose == 1:
            logger.setLevel(logging.DEBUG)
        elif options.verbose > 1:
            logger.setLevel(logging.TRACE)
    else:
        logging.basicConfig(level=logging.INFO, format='%(message)s',
                            stream=sys.stdout)

    # Locate the input file and set the output directory.
    try:
        input_path, output_dir, log_id = find_log_file(options.log, candidate_files='input.p1bin',
                                                       return_output_dir=True, return_log_id=True,
                                                       log_base_dir=options.log_base_dir)

        if log_id is None:
            logger.info('Loading %s.' % os.path.basename(input_path))
        else:
            logger.info('Loading %s from log %s.' % (input_path, log_id))

        if options.output is not None:
            output_dir = options.output
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    # If the user specified a set of message names, lookup their type values. Below, we will limit the printout to only
    # those message types.
    message_types = set()
    if options.p1bin_type is not None:
        # Pattern match to any of:
        #   -m Type1
        #   -m Type1 -m Type2
        #   -m Type1,Type2
        #   -m Type1,Type2 -m Type3
        #   -m Type*
        try:
            message_types = find_matching_p1bin_types(options.p1bin_type)
            if len(message_types) == 0:
                # find_matching_message_types() will print an error.
                sys.exit(1)
        except ValueError as e:
            _logger.error(str(e))
            sys.exit(1)

    # Parse each entry in the .p1bin file and extract its contents to 'output_dir/<prefix>.message_type.bin', where
    # message_type is the numeric type identifier.
    if options.prefix is not None:
        prefix = options.prefix
    else:
        prefix = os.path.splitext(os.path.basename(input_path))[0]

    out_files = {}

    reader = P1BinReader(input_path, show_progress=True,
                         ignore_index=options.ignore_index, message_types=message_types)
    for record in reader:
        if options.extract:
            message_type = record.message_type
            if message_type not in out_files:
                out_path = os.path.join(
                    output_dir, f'{prefix}.{message_type.name}.bin')
                out_files[message_type] = open(out_path, 'wb')
            out_files[message_type].write(record.contents)

    print(reader.message_counts)


if __name__ == "__main__":
    main()
