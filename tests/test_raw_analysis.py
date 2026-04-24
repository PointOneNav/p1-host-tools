"""Unit and integration tests for bin/raw_analysis.py."""

import argparse
import io
import os
import sys
from unittest.mock import patch

import pytest

# Add bin/ to the path so raw_analysis can be imported as a plain module.
sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'bin')))
from raw_analysis import (  # noqa: E402
    EOF_FORMAT,
    FORMAT_STRS,
    _create_framers,
    _get_index_path,
    _stream_and_index,
    find_gaps,
    generate_separated_logs,
    get_output_file_path,
    index_messages,
    is_rtcm_with_station_id,
    load_index,
    raw_analysis,
    separate_and_index,
)

# Two real NMEA sentences used across all integration tests.
NMEA_GGA = "$GPGGA,000000.000,3746.37327400,N,12224.26599800,W,2,13,2.1,3.260,M,34.210,M,11.1,0234*5B\r\n"
NMEA_RMC = "$GPRMC,000000.000,A,3746.37327400,N,12224.26599800,W,0.00,0.00,010101,,,D*76\r\n"

# Valid RTCM frames used in multi-protocol tests. Payloads are zero-padded to the standard field widths; only the 12-bit
# message number and CRC matter here.
RTCM_MSG_1005 = b'\xd3\x00\x13>\xd0\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf2K\xf4'
RTCM_MSG_1006 = b'\xd3\x00\x15>\xe0\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\xd2\x1f'


class FakeStdin:
    """Wraps a BytesIO to stand in for sys.stdin when sys.stdin.buffer is accessed."""
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


class FakeTextStdout:
    """Stands in for sys.stdout, recording text writes and exposing .buffer for binary writes."""
    def __init__(self):
        self._text = io.StringIO()
        self.buffer = io.BytesIO()

    def write(self, s: str):
        self._text.write(s)

    def flush(self):
        pass

    def getvalue(self) -> str:
        return self._text.getvalue()


def make_options(**kwargs):
    """Return an options Namespace with defaults suitable for unit testing."""
    ns = argparse.Namespace(
        format=FORMAT_STRS.copy(),
        output_dir=None,
        prefix=None,
        skip_bytes=0,
        bytes_to_process=None,
        ignore_index=False,
        extract=False,
        split_rtcm_base_id=False,
    )
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


@pytest.fixture
def nmea_file(tmp_path):
    """Write two NMEA sentences to a temp file and return its path as a string."""
    path = tmp_path / 'input.bin'
    path.write_bytes((NMEA_GGA + NMEA_RMC).encode())
    return path


@pytest.fixture
def multi_protocol_file(tmp_path):
    """Write interleaved NMEA and RTCM messages to a temp file and return its path."""
    data = NMEA_GGA.encode() + RTCM_MSG_1005 + NMEA_RMC.encode() + RTCM_MSG_1006
    path = tmp_path / 'input.bin'
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# is_rtcm_with_station_id
# ---------------------------------------------------------------------------

class TestIsRTCMID:
    def test_1005_1006(self):
        assert is_rtcm_with_station_id(1005)
        assert is_rtcm_with_station_id(1006)

    def test_msm(self):
        for i in range(1070, 1230, 10):
            for j in range(1, 8):
                assert is_rtcm_with_station_id(i + j)

    def test_non_matching_ids(self):
        assert not is_rtcm_with_station_id(1007)
        assert not is_rtcm_with_station_id(1070)
        assert not is_rtcm_with_station_id(1078)
        assert not is_rtcm_with_station_id(1300)
        assert not is_rtcm_with_station_id(999)


# ---------------------------------------------------------------------------
# get_output_file_path
# ---------------------------------------------------------------------------

class TestGetOutputFilePath:
    def test_derives_prefix_and_dir_from_input_path(self):
        assert get_output_file_path('/data/input.raw', '.nmea') == '/data/input.nmea'

    def test_explicit_output_dir_overrides_input_dir(self):
        assert get_output_file_path('/data/input.raw', '.nmea', output_dir='/out') == '/out/input.nmea'

    def test_explicit_prefix_overrides_input_stem(self):
        assert get_output_file_path('/data/input.raw', '.nmea', prefix='mylog') == '/data/mylog.nmea'

    def test_explicit_prefix_and_output_dir(self):
        result = get_output_file_path('/data/input.raw', '.nmea', output_dir='/out', prefix='mylog')
        assert result == '/out/mylog.nmea'


# ---------------------------------------------------------------------------
# _get_index_path
# ---------------------------------------------------------------------------

class TestGetIndexPath:
    def test_all_formats_produces_plain_index_name(self):
        options = make_options(format=FORMAT_STRS.copy(), prefix=None)
        assert _get_index_path('/data/input.raw', options) == '/data/input.index.csv'

    def test_single_format_appended_to_name(self):
        options = make_options(format={'nmea'}, prefix=None)
        assert _get_index_path('/data/input.raw', options) == '/data/input.index.nmea.csv'

    def test_two_formats_appear_in_sorted_order(self):
        # Set iteration order is non-deterministic; the filename must be stable.
        options = make_options(format={'rtcm', 'fe'}, prefix=None)
        assert _get_index_path('/data/input.raw', options) == '/data/input.index.fe_rtcm.csv'

    def test_respects_output_dir(self, tmp_path):
        options = make_options(format=FORMAT_STRS.copy(), output_dir=str(tmp_path), prefix=None)
        assert _get_index_path('/data/input.raw', options) == str(tmp_path / 'input.index.csv')

    def test_stdout_prefix_derives_name_from_input_file(self):
        # When prefix='-' (stdout mode), the index is named after the input file, not '-'.
        options = make_options(format=FORMAT_STRS.copy(), prefix='-')
        assert _get_index_path('/data/input.raw', options) == '/data/input.index.csv'


# ---------------------------------------------------------------------------
# load_index
# ---------------------------------------------------------------------------

class TestLoadIndex:
    def test_parses_protocol_id_offset_and_size(self, tmp_path):
        csv = tmp_path / 'test.index.csv'
        csv.write_text(
            'Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n'
            'nmea,GPGGA,0,92,\n'
            'nmea,GPRMC,92,77,\n'
            f'{EOF_FORMAT},0,169,0,\n'
        )
        index = load_index(str(csv))
        assert index[0] == ('nmea', 'GPGGA', 0, 92)
        assert index[1] == ('nmea', 'GPRMC', 92, 77)

    def test_sorts_out_of_order_entries_by_offset(self, tmp_path):
        # Framers can emit messages out of offset order during data dropouts.
        csv = tmp_path / 'test.index.csv'
        csv.write_text(
            'Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n'
            'nmea,GPRMC,92,77,\n'
            'nmea,GPGGA,0,92,\n'
            f'{EOF_FORMAT},0,169,0,\n'
        )
        index = load_index(str(csv))
        assert index[0][2] == 0    # GPGGA should sort first.
        assert index[1][2] == 92   # GPRMC should sort second.

    def test_eof_sentinel_is_included(self, tmp_path):
        csv = tmp_path / 'test.index.csv'
        csv.write_text(
            'Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n'
            f'{EOF_FORMAT},0,169,0,\n'
        )
        index = load_index(str(csv))
        assert index[-1][0] == EOF_FORMAT
        assert index[-1][2] == 169


# ---------------------------------------------------------------------------
# find_gaps
# ---------------------------------------------------------------------------

class TestFindGaps:
    def test_contiguous_entries_do_not_raise(self):
        index = [
            ('nmea', 'GPGGA', 0, 10),
            ('nmea', 'GPRMC', 10, 20),
            (EOF_FORMAT, '0', 30, 0),
        ]
        find_gaps(index)  # Must not raise.

    def test_gap_between_entries_does_not_raise(self):
        # Verify that gap detection completes without exception.
        index = [
            ('nmea', 'GPGGA', 0, 10),
            ('nmea', 'GPRMC', 15, 20),  # 5-byte gap before this entry.
        ]
        find_gaps(index)

    def test_empty_index(self):
        find_gaps([])


# ---------------------------------------------------------------------------
# _create_framers
# ---------------------------------------------------------------------------

class TestCreateFramers:
    def test_all_formats_produces_all_three_framers(self):
        options = make_options(format=FORMAT_STRS.copy())
        rtcm, fe, nmea = _create_framers(options)
        assert rtcm is not None
        assert fe is not None
        assert nmea is not None

    def test_nmea_only_returns_none_for_rtcm_and_fe(self):
        options = make_options(format={'nmea'})
        rtcm, fe, nmea = _create_framers(options)
        assert rtcm is None
        assert fe is None
        assert nmea is not None

    def test_rtcm_only_returns_none_for_fe_and_nmea(self):
        options = make_options(format={'rtcm'})
        rtcm, fe, nmea = _create_framers(options)
        assert rtcm is not None
        assert fe is None
        assert nmea is None


# ---------------------------------------------------------------------------
# _stream_and_index (integration tests using real NMEA data)
# ---------------------------------------------------------------------------

class TestStreamAndIndex:
    def _run(self, nmea_file, tmp_path, **kwargs):
        """Helper that runs _stream_and_index with NMEA-only framers and returns (index, total_bytes)."""
        content = nmea_file.read_bytes()
        options = make_options(format={'nmea'}, output_dir=str(tmp_path), **kwargs)
        rtcm_framer, fe_framer, nmea_framer = _create_framers(options)
        with open(nmea_file, 'rb') as in_fd:
            return _stream_and_index(
                input_path=str(nmea_file), in_fd=in_fd, options=options,
                rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                skip_bytes=0, bytes_to_process=len(content), file_size=len(content),
                output_map=None, index_path=None,
            ), content

    def test_returns_one_entry_per_message(self, nmea_file, tmp_path):
        (index, total_bytes), content = self._run(nmea_file, tmp_path)
        assert total_bytes == len(content)
        assert len(index) == 2

    def test_entry_contains_correct_protocol_id_offset_and_size(self, nmea_file, tmp_path):
        (index, _), _ = self._run(nmea_file, tmp_path)
        assert index[0] == ('nmea', 'GPGGA', 0, len(NMEA_GGA))
        assert index[1] == ('nmea', 'GPRMC', len(NMEA_GGA), len(NMEA_RMC))

    def test_writes_valid_index_csv(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        index_path = str(tmp_path / 'out.index.csv')
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))
        rtcm_framer, fe_framer, nmea_framer = _create_framers(options)

        with open(nmea_file, 'rb') as in_fd:
            _stream_and_index(
                input_path=str(nmea_file), in_fd=in_fd, options=options,
                rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                skip_bytes=0, bytes_to_process=len(content), file_size=len(content),
                output_map=None, index_path=index_path,
            )

        loaded = load_index(index_path)
        data_entries = [e for e in loaded if e[0] != EOF_FORMAT]
        assert len(data_entries) == 2
        assert data_entries[0] == ('nmea', 'GPGGA', 0, len(NMEA_GGA))
        assert data_entries[1] == ('nmea', 'GPRMC', len(NMEA_GGA), len(NMEA_RMC))

    def test_csv_includes_eof_sentinel(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        index_path = str(tmp_path / 'out.index.csv')
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))
        rtcm_framer, fe_framer, nmea_framer = _create_framers(options)

        with open(nmea_file, 'rb') as in_fd:
            _stream_and_index(
                input_path=str(nmea_file), in_fd=in_fd, options=options,
                rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                skip_bytes=0, bytes_to_process=len(content), file_size=len(content),
                output_map=None, index_path=index_path,
            )

        loaded = load_index(index_path)
        assert loaded[-1][0] == EOF_FORMAT
        assert loaded[-1][2] == len(content)

    def test_extracts_nmea_messages_to_output_file(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        out_file = tmp_path / 'output.nmea'
        options = make_options(format={'nmea'}, output_dir=str(tmp_path),
                               extract=True, split_rtcm_base_id=False)
        rtcm_framer, fe_framer, nmea_framer = _create_framers(options, return_bytes=True)
        output_map = {'nmea': open(str(out_file), 'wt')}

        with open(nmea_file, 'rb') as in_fd:
            _stream_and_index(
                input_path=str(nmea_file), in_fd=in_fd, options=options,
                rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                skip_bytes=0, bytes_to_process=len(content), file_size=len(content),
                output_map=output_map, index_path=None,
            )
        output_map['nmea'].close()

        # Use read_bytes().decode() rather than read_text() to avoid universal-newline
        # translation stripping the \r from NMEA's \r\n terminators.
        assert out_file.read_bytes().decode() == NMEA_GGA + NMEA_RMC

    def test_skip_bytes_shifts_absolute_offset_in_index(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        skip = len(NMEA_GGA)
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))
        rtcm_framer, fe_framer, nmea_framer = _create_framers(options)

        with open(nmea_file, 'rb') as in_fd:
            in_fd.seek(skip)
            index, total_bytes = _stream_and_index(
                input_path=str(nmea_file), in_fd=in_fd, options=options,
                rtcm_framer=rtcm_framer, fe_framer=fe_framer, nmea_framer=nmea_framer,
                skip_bytes=skip, bytes_to_process=len(NMEA_RMC), file_size=len(content),
                output_map=None, index_path=None,
            )

        # Only the second message should be indexed, but its offset is absolute within the file.
        assert len(index) == 1
        assert index[0] == ('nmea', 'GPRMC', len(NMEA_GGA), len(NMEA_RMC))


# ---------------------------------------------------------------------------
# index_messages (integration tests)
# ---------------------------------------------------------------------------

class TestIndexMessages:
    def test_generates_index_file_and_returns_entries(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path), ignore_index=True)
        index, _ = index_messages(str(nmea_file), options)

        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        assert len(data_entries) == 2
        assert (tmp_path / 'input.index.nmea.csv').exists()

    def test_reuses_valid_existing_index_without_reparsing(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))

        # First call generates the index file.
        index_messages(str(nmea_file), options)
        index_file = tmp_path / 'input.index.nmea.csv'
        mtime_before = index_file.stat().st_mtime

        # Second call should return without modifying the index file.
        index_messages(str(nmea_file), options)
        assert index_file.stat().st_mtime == mtime_before

    def test_ignores_index_whose_byte_count_does_not_match(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))
        index_messages(str(nmea_file), options)

        # Shorten the input so that bytes_to_process changes on the next call.
        nmea_file.write_bytes(NMEA_GGA.encode())

        index, _ = index_messages(str(nmea_file), options)
        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        assert len(data_entries) == 1

    def test_ignores_index_missing_eof_sentinel(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))

        # Write an index file that was truncated before the EOF sentinel was written.
        index_file = tmp_path / 'input.index.nmea.csv'
        index_file.write_text(
            'Protocol, ID, Offset (Bytes), Length (Bytes), P1 Time\n'
            'nmea,GPGGA,0,92,\n'
        )

        # The incomplete index should be discarded and a fresh one generated.
        index, _ = index_messages(str(nmea_file), options)
        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        assert len(data_entries) == 2

    def test_ignore_index_flag_forces_regeneration(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))

        # First call creates the index.
        index_messages(str(nmea_file), options)
        index_file = tmp_path / 'input.index.nmea.csv'
        mtime_before = index_file.stat().st_mtime

        # With ignore_index=True the file should be regenerated.
        options.ignore_index = True
        index_messages(str(nmea_file), options)
        assert index_file.stat().st_mtime >= mtime_before


# ---------------------------------------------------------------------------
# generate_separated_logs (integration tests)
# ---------------------------------------------------------------------------

class TestGenerateSeparatedLogs:
    def test_writes_all_messages_to_nmea_file(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        index = [
            ('nmea', 'GPGGA', 0, len(NMEA_GGA)),
            ('nmea', 'GPRMC', len(NMEA_GGA), len(NMEA_RMC)),
            (EOF_FORMAT, '0', len(content), 0),
        ]
        options = make_options(format={'nmea'}, output_dir=str(tmp_path),
                               prefix=nmea_file.stem, split_rtcm_base_id=False)

        generate_separated_logs(str(nmea_file), index, options)

        out_file = tmp_path / 'input.nmea'
        assert out_file.exists()
        assert out_file.read_bytes() == content

    def test_skips_protocols_absent_from_format(self, nmea_file, tmp_path):
        # An 'fe' index entry should be silently ignored when format contains only 'nmea'.
        index = [
            ('fe', '10001', 0, len(NMEA_GGA)),
            ('nmea', 'GPRMC', len(NMEA_GGA), len(NMEA_RMC)),
        ]
        options = make_options(format={'nmea'}, output_dir=str(tmp_path),
                               prefix=nmea_file.stem, split_rtcm_base_id=False)

        generate_separated_logs(str(nmea_file), index, options)

        out_file = tmp_path / 'input.nmea'
        assert out_file.read_bytes() == NMEA_RMC.encode()

    def test_eof_sentinel_is_skipped(self, nmea_file, tmp_path):
        content = nmea_file.read_bytes()
        index = [
            ('nmea', 'GPGGA', 0, len(NMEA_GGA)),
            (EOF_FORMAT, '0', len(content), 0),
        ]
        options = make_options(format={'nmea'}, output_dir=str(tmp_path),
                               prefix=nmea_file.stem, split_rtcm_base_id=False)

        generate_separated_logs(str(nmea_file), index, options)

        out_file = tmp_path / 'input.nmea'
        assert out_file.read_bytes() == NMEA_GGA.encode()


# ---------------------------------------------------------------------------
# separate_and_index (integration tests)
# ---------------------------------------------------------------------------

class TestSeparateAndIndex:
    def test_indexes_all_messages_in_file(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path), prefix=nmea_file.stem)
        index, total_bytes = separate_and_index(str(nmea_file), options)

        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        assert len(data_entries) == 2
        assert total_bytes == len(nmea_file.read_bytes())

    def test_extracts_nmea_to_output_file(self, nmea_file, tmp_path):
        options = make_options(format={'nmea'}, output_dir=str(tmp_path),
                               prefix=nmea_file.stem, extract=True, split_rtcm_base_id=False)
        separate_and_index(str(nmea_file), options)

        out_file = tmp_path / (nmea_file.stem + '.nmea')
        assert out_file.exists()
        # Use read_bytes().decode() rather than read_text() to avoid universal-newline
        # translation stripping the \r from NMEA's \r\n terminators.
        assert out_file.read_bytes().decode() == NMEA_GGA + NMEA_RMC

    def test_indexes_interleaved_nmea_and_rtcm(self, multi_protocol_file, tmp_path):
        options = make_options(format={'nmea', 'rtcm'}, output_dir=str(tmp_path),
                               prefix=multi_protocol_file.stem)
        index, _ = separate_and_index(str(multi_protocol_file), options)

        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        nmea_entries = [e for e in data_entries if e[0] == 'nmea']
        rtcm_entries = [e for e in data_entries if e[0] == 'rtcm']
        assert len(nmea_entries) == 2
        assert len(rtcm_entries) == 2

    def test_extracts_interleaved_protocols_to_separate_files(self, multi_protocol_file, tmp_path):
        options = make_options(format={'nmea', 'rtcm'}, output_dir=str(tmp_path),
                               prefix=multi_protocol_file.stem, extract=True, split_rtcm_base_id=False)
        separate_and_index(str(multi_protocol_file), options)

        nmea_out = tmp_path / 'input.nmea'
        rtcm_out = tmp_path / 'input.rtcm3'
        assert nmea_out.exists()
        assert rtcm_out.exists()
        assert nmea_out.read_bytes().decode() == NMEA_GGA + NMEA_RMC
        assert rtcm_out.read_bytes() == RTCM_MSG_1005 + RTCM_MSG_1006


def make_raw_analysis_options(**kwargs):
    """Return an options Namespace with defaults suitable for passing to raw_analysis()."""
    ns = argparse.Namespace(
        log='-',
        format=None,
        verbose=0,
        bytes_to_process=None,
        skip_bytes=0,
        ignore_index=False,
        extract=False,
        split_rtcm_base_id=False,
        check_gaps=False,
        output_dir=None,
        prefix=None,
        log_base_dir='/tmp',
        p1bin_type=None,
    )
    for key, value in kwargs.items():
        setattr(ns, key, value)
    return ns


# ---------------------------------------------------------------------------
# separate_and_index (stdin/stdout tests)
# ---------------------------------------------------------------------------

class TestSeparateAndIndexStdin:
    def test_indexes_nmea_from_stdin(self, tmp_path):
        data = (NMEA_GGA + NMEA_RMC).encode()
        options = make_options(format={'nmea'}, output_dir=str(tmp_path))

        with patch('sys.stdin', FakeStdin(data)):
            index, total_bytes = separate_and_index('-', options)

        data_entries = [e for e in index if e[0] != EOF_FORMAT]
        assert len(data_entries) == 2
        assert total_bytes == len(data)

    def test_extracts_nmea_from_stdin_to_file(self, tmp_path):
        data = (NMEA_GGA + NMEA_RMC).encode()
        options = make_options(format={'nmea'}, output_dir=str(tmp_path), prefix='output',
                               extract=True, split_rtcm_base_id=False)

        with patch('sys.stdin', FakeStdin(data)):
            separate_and_index('-', options)

        out_file = tmp_path / 'output.nmea'
        assert out_file.exists()
        assert out_file.read_bytes().decode() == NMEA_GGA + NMEA_RMC

    def test_extracts_nmea_from_stdin_to_stdout(self):
        data = (NMEA_GGA + NMEA_RMC).encode()
        options = make_options(format={'nmea'}, prefix='-', extract=True, split_rtcm_base_id=False)
        fake_stdout = FakeTextStdout()

        with patch('sys.stdin', FakeStdin(data)), patch('sys.stdout', fake_stdout):
            separate_and_index('-', options)

        assert fake_stdout.getvalue() == NMEA_GGA + NMEA_RMC


# ---------------------------------------------------------------------------
# raw_analysis (application-level tests)
# ---------------------------------------------------------------------------

class TestRawAnalysis:
    def test_stdin_indexes_nmea_without_error(self, tmp_path):
        data = (NMEA_GGA + NMEA_RMC).encode()
        # Use an explicit prefix so output goes to a file rather than stdout.
        options = make_raw_analysis_options(
            log='-', format=['nmea'], prefix='out', output_dir=str(tmp_path), check_gaps=False)

        with patch('sys.stdin', FakeStdin(data)):
            raw_analysis(options)  # Must not raise or call sys.exit.

    def test_stdin_multiple_formats_to_stdout_exits(self):
        # Multiple formats cannot be multiplexed onto a single stdout stream; raw_analysis must
        # exit before attempting to read stdin.
        options = make_raw_analysis_options(log='-', format=['nmea', 'rtcm'])

        with pytest.raises(SystemExit):
            raw_analysis(options)

    def test_invalid_format_exits(self, tmp_path):
        options = make_raw_analysis_options(
            log='-', format=['not_a_format'], prefix='out', output_dir=str(tmp_path))

        with patch('sys.stdin', FakeStdin(b'')):
            with pytest.raises(SystemExit):
                raw_analysis(options)

    def test_file_input_creates_index(self, nmea_file, tmp_path):
        options = make_raw_analysis_options(
            log='some_log', format=['nmea'], output_dir=str(tmp_path), check_gaps=False)

        with patch('raw_analysis.find_log_file', return_value=(str(nmea_file), str(tmp_path), None)):
            raw_analysis(options)

        assert (tmp_path / 'input.index.nmea.csv').exists()

    def test_file_input_with_extract_creates_output_files(self, nmea_file, tmp_path):
        options = make_raw_analysis_options(
            log='some_log', format=['nmea'], output_dir=str(tmp_path),
            extract=True, check_gaps=False, split_rtcm_base_id=False)

        with patch('raw_analysis.find_log_file', return_value=(str(nmea_file), str(tmp_path), None)):
            raw_analysis(options)

        assert (tmp_path / 'input.nmea').exists()
