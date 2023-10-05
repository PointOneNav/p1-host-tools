from bisect import bisect_right
import copy
from datetime import datetime
import io
import os
import sys
from typing import Dict, Iterable, Optional, Union

import numpy as np
from construct import Struct, Int32ul, Int8ul, Switch, Padding, Const, Int16ul, Bytes, this, StreamError

from fusion_engine_client.messages.defs import MessagePayload, Timestamp
from fusion_engine_client.parsers import file_index
from fusion_engine_client.utils.time_range import TimeRange
from fusion_engine_client.utils.construct_utils import AutoEnum

from . import trace as logging
from .p1bin_type import P1BinType, P1BinRecord

_p1bin_timestamp = Struct(
    "time_seconds" / Int32ul,
    "time_fraction_ns" / Int32ul,
)

_p1bin_file_header = Struct(
    "file_entry_header_version" / Const(1, Int8ul),
    "unix_serialization_time" / _p1bin_timestamp,
)

_p1bin_message_header = Struct(
    "message_header_version" / Int8ul,
    # Version changed after adding padding byte.
    Switch(this.message_header_version, {1: Padding(0), 2: Padding(1)}),
    "message_type" / AutoEnum(Int16ul, P1BinType),
    "payload_size_bytes" / Int32ul,
    "source_identifier" / Int32ul,
)

_p1bin_entry = Struct(
    "file_header" / _p1bin_file_header,
    "message_header" / _p1bin_message_header,
    "contents" / Bytes(this.message_header.payload_size_bytes),
)

_p1bin_api_version = Struct(
    "api_version" / Int8ul,
)

API_VERSION = 1


class _DummyTimeMessage(MessagePayload):
    MESSAGE_TYPE = 0xFFFF

    def __init__(self, p1bin_timestamp):
        self.timestamp = p1bin_timestamp.unix_serialization_time.time_seconds + \
            p1bin_timestamp.unix_serialization_time.time_fraction_ns * 1e-9

    def get_p1_time(self) -> Optional[Timestamp]:
        return None

    def get_system_time_ns(self) -> float:
        return self.timestamp


def _record_from_construct(record):
    return P1BinRecord(
        record.file_header.unix_serialization_time.time_seconds +
        record.file_header.unix_serialization_time.time_fraction_ns * 1e-9,
        record.message_header.message_type,
        record.contents
    )


class P1BinReader(object):
    """!
    @brief Generator class for iterating through entries in P1Bin file.
    """
    logger = logging.getLogger('point_one.p1bin_reader')

    def __init__(self, input_file, show_progress: bool = False,
                 generate_index: bool = True, ignore_index: bool = False, max_bytes: Optional[int] = None,
                 time_range: Optional[TimeRange] = None, message_types: Optional[Union[Iterable[P1BinType], P1BinType]] = None,
                 return_offset: bool = False, return_message_index: bool = False):
        """!
        @brief Construct a new generator instance.

        Each call to @ref next() will return a tuple containing any/all of the message record, byte offset, and message
        index depending on the values of the `return_*` parameters.

        @param input_file The path to an input file (`.p1bin`), or an open file-like object.
        @param show_progress If `True`, print file read progress to the console periodically.
        @param generate_index If `True`, generate an index file if one does not exist for faster reading in the future.
               See @ref FileIndex for details. Ignored if `max_bytes` is specified.
        @param ignore_index If `True`, ignore the existing index file and read from the binary file directly. If
               `generate_index == True`, this will delete the existing file and create a new one.
        @param max_bytes If specified, read up to the maximum number of bytes.
        @param time_range An optional @ref TimeRange object specifying desired start and end time bounds of the data to
               be read. The range must be relative since P1Bin does not ensure valid absolute time. See @ref TimeRange
               for more details.
        @param message_types A list of one or more @ref fusion_engine_client.messages.defs.P1BinType "P1BinTypes" to
               be returned. If `None` or an empty list, read all available messages.
        @param return_offset If `True`, return the offset into the file (in bytes) at which the message began.
        @param return_message_index If `True`, return the 0-based index of the message within the file.
        """
        self.return_offset = return_offset
        self.return_message_index = return_message_index

        if time_range is not None and time_range.absolute:
            raise ValueError('P1BinReader does not support absolute "time_range".')

        self._original_time_range = copy.deepcopy(time_range)
        self.time_range = copy.deepcopy(self._original_time_range)

        if message_types is None:
            self.message_types = None
        elif isinstance(message_types, P1BinType):
            self.message_types = set((message_types,))
        else:
            self.message_types = set(t for t in message_types)
            if len(self.message_types) == 0:
                self.message_types = None

        self._original_message_types = copy.deepcopy(self.message_types)
        self.filtered_message_types = self.message_types is not None

        self.valid_count = 0
        self.message_counts: Dict[P1BinType, int] = {}
        self.total_bytes_read = 0
        self.current_message_index = 0

        self.show_progress = show_progress
        self.last_print_bytes = 0
        self.start_time = datetime.now()

        # Open the file to be read.
        if isinstance(input_file, str):
            self.input_file = open(input_file, 'rb')
        else:
            self.input_file = input_file

        input_path = self.input_file.name
        self.file_size_bytes = os.stat(input_path).st_size

        if max_bytes is None:
            self.max_bytes = sys.maxsize
        else:
            self.max_bytes = max_bytes
            if generate_index:
                self.logger.debug(
                    'Max bytes specified. Disabling index generation.')
                generate_index = False

        # Open the companion index file if one exists.
        self.index_path = file_index.FileIndex.get_path(input_path)
        self._original_index = None
        self.index = None
        self.next_index_elem = 0
        if ignore_index:
            if os.path.exists(self.index_path):
                if generate_index:
                    self.logger.debug(
                        "Deleting/regenerating index file @ '%s'." % self.index_path)
                    os.remove(self.index_path)
                else:
                    self.logger.debug(
                        "Ignoring index file @ '%s'." % self.index_path)
        else:
            if os.path.exists(self.index_path):
                try:
                    self.logger.debug(
                        "Loading index file '%s'." % self.index_path)
                    self._original_index = file_index.FileIndex(index_path=self.index_path, data_path=input_path,
                                                                delete_on_error=generate_index)
                    self.index = self._original_index[self.message_types][self.time_range]
                    self.filtered_message_types = len(np.unique(self._original_index.type)) != \
                        len(np.unique(self.index.type))
                except ValueError as e:
                    self.logger.error("Error loading index file: %s" % str(e))
            else:
                self.logger.debug("No index file found @ '%s'." %
                                  self.index_path)

        self.index_builder = None
        self.set_generate_index(generate_index)
        self.rewind()

    def rewind(self):
        self.logger.debug('Rewinding to the start of the file.')

        if self.time_range is not None:
            self.time_range.restart()

        if self._original_time_range is not None:
            self._original_time_range.restart()

        self.valid_count = 0
        self.message_counts = {}

        self.last_print_bytes = 0
        self.start_time = datetime.now()

        self.next_index_elem = 0
        self.input_file.seek(0, os.SEEK_SET)

        if self.file_size_bytes > 0:
            api_version = _p1bin_api_version.parse_stream(
                self.input_file).api_version
            if api_version != 1:
                raise RuntimeError(
                    f'Unsupported P1Bin api_version: {api_version}.')
        self.total_bytes_read = 1

        if self.index_builder is not None:
            self.index_builder = file_index.FileIndexBuilder()

    def seek_to_message(self, message_index: int, is_filtered_index: bool = False):
        if self.index is None:
            raise NotImplemented(
                'A file index is required to seek by message index.')

        max_index = len(self.index) if is_filtered_index else len(
            self._original_index)
        if message_index < 0 or message_index >= max_index:
            raise ValueError('Invalid message index.')

        if not is_filtered_index:
            self.clear_filters()
        self.next_index_elem = message_index

    def seek_to_eof(self):
        self._read_next(force_eof=True)

    def reached_eof(self):
        if self.index is None:
            return self.total_bytes_read == self.file_size_bytes
        else:
            return self.next_index_elem == len(self.index)

    def have_index(self):
        return self._original_index is not None

    def get_index(self):
        return self._original_index

    def generating_index(self):
        return self.index_builder is not None

    def set_generate_index(self, generate_index):
        if self._original_index is None:
            if generate_index:
                self.logger.debug("Generating index file '%s'." %
                                  self.index_path)
                self.index_builder = file_index.FileIndexBuilder()
            else:
                self.logger.debug("Index generation disabled.")
                self.index_builder = None

    def set_show_progress(self, show_progress):
        self.show_progress = show_progress

    def set_max_bytes(self, max_bytes):
        if max_bytes is None:
            self.max_bytes = sys.maxsize
        else:
            self.max_bytes = max_bytes
            if self.index_builder is not None:
                self.logger.debug(
                    'Max bytes specified. Disabling index generation.')
                self.set_generate_index(False)

    def get_bytes_read(self):
        return self.total_bytes_read

    def next(self):
        return self.read_next()

    def read_next(self, require_p1_time=False, require_system_time=False, generate_index=True):
        return self._read_next(require_p1_time=require_p1_time, require_system_time=require_system_time,
                               generate_index=generate_index)

    def _read_next(self, require_p1_time=False, require_system_time=False, generate_index=True, force_eof=False):
        if force_eof:
            if not self.reached_eof():
                if self.generating_index():
                    raise ValueError(
                        'Cannot jump to EOF while building an index file.')

                self.logger.debug('Forcibly seeking to EOF.')
                if self.index is None:
                    self.input_file.seek(self.file_size_bytes, os.SEEK_SET)
                    self.total_bytes_read = self.file_size_bytes
                elif len(self.index) == 0:
                    self.next_index_elem = 0
                    self.total_bytes_read = 0
                else:
                    # Read the header of the last element so we can set total_bytes_read equal to the end of the index.
                    # We're not actually going to return this message.
                    offset_bytes = self.index.offset[-1]
                    self.input_file.seek(offset_bytes, os.SEEK_SET)
                    _p1bin_entry.parse_stream(self.input_file)
                    self.total_bytes_read = self.input_file.tell()
                    self.next_index_elem = len(self.index)
            else:
                return

        while True:
            if self.index is not None:
                if self.next_index_elem == len(self.index):
                    # End of file.
                    self.logger.debug('EOF reached.')
                    break
                else:
                    offset_bytes = int(self.index.offset[self.next_index_elem])
                    self.current_message_index = self.index.message_index[self.next_index_elem]
                    self.next_index_elem += 1
                    self.input_file.seek(offset_bytes, os.SEEK_SET)
                    self.total_bytes_read = offset_bytes

            start_offset_bytes = self.total_bytes_read
            try:
                record = _record_from_construct(
                    _p1bin_entry.parse_stream(self.input_file))
                self.total_bytes_read = self.input_file.tell()
            except StreamError:
                # End of file.
                self.logger.debug('EOF reached.')
                break

            self._print_progress()

            if self.total_bytes_read > self.max_bytes:
                self.logger.debug(
                    'Max read length exceeded (%d B).' % self.max_bytes)
                break

            if self.logger.isEnabledFor(logging.getTraceLevel(depth=2)):
                self.logger.trace('Reading candidate message @ %d (0x%x).' % (start_offset_bytes, start_offset_bytes),
                                  depth=2)

            self.valid_count += 1
            if self.logger.isEnabledFor(logging.getTraceLevel(depth=1)):
                self.logger.trace('Read %s message @ %d (0x%x). [length=%d B, # messages=%d]' %
                                  (record.message_type, start_offset_bytes, start_offset_bytes,
                                   len(record.contents), self.valid_count),
                                  depth=1)

            current_message_index = self.current_message_index
            self.current_message_index += 1

            # Add this message to the index file.
            if self.index_builder is not None and generate_index:
                self.index_builder.append(message_type=record.message_type, offset_bytes=start_offset_bytes,
                                          p1_time=record.unix_serialization_time)

            # Now, if this message is not in the user-specified filter criteria, skip it.
            #
            # If we have an index available, this is implied by the index (we won't seek to messages that don't meet
            # the criteria at all), so we do not need to do this check. Further, self.message_types and
            # self.time_range are _only_ valid if we are _not_ using an index, so this may end up incorrectly
            # filtering out some messages as unwanted.
            if self.index is None:
                if self.message_types is not None and record.message_type not in self.message_types:
                    self.logger.trace(
                        "Message type not requested. Skipping.", depth=1)
                    continue
                elif self.time_range is not None and not self.time_range.is_in_range(_DummyTimeMessage(record.unix_serialization_time)):
                    if self.time_range.in_range_started() and (self.index_builder is None or not generate_index):
                        self.logger.debug(
                            "End of time range reached. Finished processing.")
                        break
                    else:
                        self.logger.trace(
                            "Message not in time range. Skipping.", depth=1)
                        continue

            self.message_counts.setdefault(record.message_type, 0)
            self.message_counts[record.message_type] += 1

            # Construct the result. If we're returning the payload, deserialize the payload.
            result = [record]
            if self.return_offset:
                result.append(start_offset_bytes)
            if self.return_message_index:
                result.append(current_message_index)

            if len(result) == 1:
                return result[0]
            else:
                return result

        # Out of the loop - EOF reached.
        self._print_progress(self.total_bytes_read)
        self.logger.debug("Read %d bytes total." % self.total_bytes_read)

        # If we are creating an index file, save it now.
        if self.index_builder is not None and generate_index:
            self.logger.debug("Saving index file as '%s'." % self.index_path)
            self._original_index = self.index_builder.save(
                self.index_path, self.input_file.name)
            self.index_builder = None

            self.index = self._original_index[self.message_types][self.time_range]
            self.message_types = None
            self.time_range = None
            self.next_index_elem = len(self.index)

        # Finished iterating.
        if force_eof:
            return
        else:
            raise StopIteration()

    def _print_progress(self, file_size=None):
        show_progress = self.show_progress

        # If this function is being called when we're done reading (file_size not None), and we used an index file which
        # did not have any entries for the requested set of data filters, don't print an info print stating "processed
        # 0/0 bytes". It's more confusing than helpful.
        if file_size is not None and self.index is not None and self.total_bytes_read == 0:
            show_progress = False

        if file_size is None:
            file_size = min(self.file_size_bytes, self.max_bytes)

        if self.total_bytes_read - self.last_print_bytes > 10e6 or self.total_bytes_read == file_size:
            elapsed_sec = (datetime.now() - self.start_time).total_seconds()
            self.logger.log(logging.INFO if show_progress else logging.DEBUG,
                            'Processed %d/%d bytes (%.1f%%). [elapsed=%.1f sec, rate=%.1f MB/s]' %
                            (self.total_bytes_read, file_size,
                             100.0 if file_size == 0 else 100.0 *
                             float(self.total_bytes_read) / file_size,
                             elapsed_sec, (self.total_bytes_read / elapsed_sec / 1e6) if elapsed_sec > 0 else np.nan))
            self.last_print_bytes = self.total_bytes_read

    def parse_entry_at_index(self, index: file_index.FileIndexEntry):
        """!
        @brief Generate payload from index entry.

        @param index The index entry at which to parse the class's input file.

        @return The @ref P1BinRecord at the index entry.
        """
        # Jump to offset governed by index.
        self.input_file.seek(index.offset, os.SEEK_SET)

        return _record_from_construct(_p1bin_entry.parse_stream(self.input_file))

    def clear_filters(self):
        self.filter_in_place(key=None, clear_existing=True)

    def filter_in_place(self, key, clear_existing: bool = False):
        """!
        @brief Limit the returned messages by type or time.

        @warning
        This operator modifies this class in-place.

        @param key One of the following:
               - An individual @ref P1BinType to be returned
               - An iterable listing one or more @ref P1BinType%s to be returned
               - A `slice` specifying the start/end of the desired absolute (P1) or relative time range
               - A @ref TimeRange object
        @param clear_existing If `True`, clear any previous filter criteria.

        @return A reference to this class.
        """
        # If we're reading using an index, determine the offset within the data file of the most recent message we have
        # read. Then below, after we filter the index down (or clear existing filtering), we'll locate the next entry to
        # be read in the file that meets the new criteria. That way we continue where we left off.
        #
        # If we're reading directly from the file without an index, we'll just pick up where the current seek is, so no
        # need to do anything special.
        if self.index is not None:
            if self.next_index_elem == 0:
                prev_offset_bytes = -1
            else:
                # Note that next_index_elem refers to the _next_ message to be read. We want the offset of the message
                # that we just read.
                prev_offset_bytes = self.index.offset[self.next_index_elem - 1]

        # If requested, clear previous filter criteria.
        if clear_existing:
            if self.index is None:
                self.message_types = copy.deepcopy(
                    self._original_message_types)
                self.time_range = copy.deepcopy(self._original_time_range)
            else:
                self.index = self._original_index

        # No key specified (convenience case).
        if key is None:
            pass
        # If we have an index file available, reduce the index to the requested criteria.
        elif self.index is not None:
            self.index = self.index[key]
            self.filtered_message_types = len(np.unique(self._original_index.type)) != \
                len(np.unique(self.index.type))
        # Otherwise, store the criteria and apply them while reading.
        else:
            # Return entries for a specific message type.
            if isinstance(key, P1BinType):
                self.message_types = set((key,))
                self.filtered_message_types = True
            # Return entries for a list of message types.
            elif isinstance(key, (set, list, tuple)) and len(key) > 0 and isinstance(next(iter(key)), P1BinType):
                new_message_types = {t for t in key if t is not None}
                if self.message_types is None:
                    self.message_types = new_message_types
                else:
                    self.message_types = self.message_types & new_message_types
                self.filtered_message_types = True
            # Key is a slice in time. Return a subset of the data.
            elif isinstance(key, slice) and (isinstance(key.start, (Timestamp, float)) or
                                             isinstance(key.stop, (Timestamp, float))):
                time_range = TimeRange(
                    start=key.start, end=key.stop, absolute=isinstance(key.start, Timestamp))
                if self.time_range is None:
                    self.time_range = time_range
                else:
                    self.time_range.intersect(time_range, in_place=True)
            # Key is a slice by index (# of messages). Return a subset of the index file.
            #
            # Note: Slicing is not supported if there is no index file. Slicing with an index file is handled above.
            elif isinstance(key, slice) and (isinstance(key.start, int) or isinstance(key.stop, int)):
                raise ValueError(
                    'Index slicing not supported when an index file is not present.')
            # Key is a TimeRange object. Return a subset of the data. All nan elements (messages without P1 time) will
            # be included in the results.
            elif isinstance(key, TimeRange):
                if self.time_range is None:
                    self.time_range = key
                else:
                    self.time_range.intersect(key, in_place=True)

        # Now, find the next entry in the newly filtered index starting after the most recent message we read. That
        # way we can continue reading where we left off.
        if self.index is not None:
            if len(self.index) == 0:
                self.next_index_elem = 0
            else:
                idx = np.argmax(self.index.offset > prev_offset_bytes)
                if idx == 0 and self.index.offset[0] <= prev_offset_bytes:
                    self.next_index_elem = len(self.index)
                else:
                    self.next_index_elem = idx

        return self

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    @classmethod
    def generate_index_file(cls, input_file, show_progress=False, ignore_index=False):
        reader = P1BinReader(input_file=input_file, show_progress=show_progress,
                             ignore_index=ignore_index, generate_index=True)
        if reader.index is None:
            for _ in reader:
                pass


class P1BinFileStream:
    """!
    @brief Class to access the contents of a p1bin file for a certain @ref P1BinType as if it were a continuous file.
    """
    def __init__(self, file_path, p1bin_type, ignore_index=False, show_read_progress=False):
        self.filtered_offsets = np.array([], dtype=np.uint64)
        self.filtered_size_bytes = 0
        self.offset = 0
        self.cur_record_contents = b''
        self.reader = P1BinReader(
            file_path, show_progress=show_read_progress, ignore_index=ignore_index, message_types=p1bin_type)
        self._index_offsets()

    def _index_offsets(self):
        sizes = [0]
        # This iterator implicitly builds the index to use later in seek.
        for record in self.reader:
            sizes.append(len(record.contents))
        cum_sum = np.array(sizes).cumsum()
        self.filtered_size_bytes = int(cum_sum[-1])
        self.filtered_offsets = cum_sum[:-1]
        self.reader.rewind()

    def seek(self, offset, whence):
        self.cur_record_contents = b''
        if whence == io.SEEK_CUR:
            offset += self.offset
        elif whence == io.SEEK_END:
            offset = self.filtered_size_bytes - offset

        if offset >= self.filtered_size_bytes:
            self.offset = self.filtered_size_bytes
            self.reader.seek_to_eof()
        else:
            self.offset = offset
            # Find the idx where the its offset is <= the search offset.
            idx = bisect_right(self.filtered_offsets, offset) - 1
            self.reader.seek_to_message(idx, is_filtered_index=True)
            extra_offset = int(offset - self.filtered_offsets[idx])
            if extra_offset != 0:
                record = self.reader.read_next()
                self.cur_record_contents = record.contents[extra_offset:]

    def tell(self):
        return self.offset

    def read(self, read_len):
        ret = b''
        while not self.reader.reached_eof():
            if read_len <= len(self.cur_record_contents):
                ret += self.cur_record_contents[:read_len]
                self.cur_record_contents = self.cur_record_contents[read_len:]
                self.offset += read_len
                break
            else:
                ret += self.cur_record_contents
                read_len -= len(self.cur_record_contents)
                self.offset += len(self.cur_record_contents)
                try:
                    record = self.reader.read_next()
                    self.cur_record_contents = record.contents
                except StopIteration:
                    break
        return ret
