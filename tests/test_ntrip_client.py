import asyncio
import socket
import time

import pytest

from p1_runner import ntrip_client
from p1_runner.ntrip_client import NTRIPClient


class FakeStream:
    """Refuses the first `refusals` connections, then connects and never sends data."""
    attempts = 0
    in_flight = 0
    max_in_flight = 0
    refusals = 3
    error = ConnectionRefusedError(111, 'Connection refused')
    eof_reads = 0  # getRawData() calls that return b'' (the caster closed a non-chunked stream)
    sent = []

    async def requestNtripStream(self, **kwargs):
        cls = FakeStream
        cls.attempts += 1
        cls.in_flight += 1
        cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        try:
            await asyncio.sleep(0.01)
            if cls.attempts <= cls.refusals:
                raise cls.error
        finally:
            cls.in_flight -= 1

    async def sendRtcmFrame(self, data):
        FakeStream.sent.append(data)

    async def getRawData(self, max_length=1024):
        if FakeStream.eof_reads > 0:
            FakeStream.eof_reads -= 1
            return b''
        await asyncio.sleep(10)

    async def closeNtripConnection(self):
        pass


@pytest.fixture
def fake_stream(monkeypatch):
    FakeStream.attempts = FakeStream.in_flight = FakeStream.max_in_flight = 0
    FakeStream.sent = []
    FakeStream.refusals = 3
    FakeStream.error = ConnectionRefusedError(111, 'Connection refused')
    FakeStream.eof_reads = 0
    monkeypatch.setattr(ntrip_client.ntripstreams, 'NtripStream', FakeStream)
    monkeypatch.setattr(NTRIPClient, 'CONNECT_RETRY_DELAY_SEC', 0.05)
    return FakeStream


def _wait(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_reconnects_after_refused_connections(fake_stream):
    client = NTRIPClient(url='http://localhost:2101', mountpoint='TEST')
    client.daemon = True
    client.start()
    try:
        assert _wait(client.is_connected), 'client never connected after the server started accepting'
        time.sleep(0.3)  # any duplicate connect coroutine would keep attempting
        assert fake_stream.attempts == fake_stream.refusals + 1
        assert fake_stream.max_in_flight == 1
    finally:
        client.stop()
        client.join(timeout=5)


def test_position_set_before_connecting_is_sent_on_connect(fake_stream):
    fake_stream.refusals = 1
    client = NTRIPClient(url='http://localhost:2101', mountpoint='TEST')
    client.daemon = True
    assert client.send_position([48.88, 2.38, 120.0]) is False  # not connected yet: kept for the connection
    client.start()
    try:
        assert _wait(lambda: fake_stream.sent), 'the position never reached the caster'
        gga = fake_stream.sent[0].decode()
        assert gga.startswith('$GPGGA,') and ',4852.80000000,N,' in gga and ',E,1,' in gga and gga.endswith('\r\n')
    finally:
        client.stop()
        client.join(timeout=5)


@pytest.mark.parametrize('error', [
    socket.gaierror(-2, 'Name or service not known'),
    OSError(101, 'Network is unreachable'),
    TimeoutError('timed out'),
], ids=['dns', 'unreachable', 'timeout'])
def test_reconnects_after_network_errors(fake_stream, error):
    fake_stream.refusals = 1
    fake_stream.error = error
    client = NTRIPClient(url='http://localhost:2101', mountpoint='TEST')
    client.daemon = True
    client.start()
    try:
        assert _wait(client.is_connected), 'client stopped retrying after %r' % error
        assert fake_stream.attempts == 2
    finally:
        client.stop()
        client.join(timeout=5)


def test_reconnects_when_the_caster_closes_a_non_chunked_stream(fake_stream):
    fake_stream.refusals = 0
    fake_stream.eof_reads = 1
    received = []
    client = NTRIPClient(url='http://localhost:2101', mountpoint='TEST', version=1, data_callback=received.append)
    client.daemon = True
    client.start()
    try:
        assert _wait(lambda: fake_stream.attempts == 2), 'client did not reconnect after the stream ended'
        assert _wait(client.is_connected)
        assert b'' not in received
    finally:
        client.stop()
        client.join(timeout=5)
