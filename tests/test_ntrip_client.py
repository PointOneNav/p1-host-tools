import asyncio
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

    async def requestNtripStream(self, **kwargs):
        cls = FakeStream
        cls.attempts += 1
        cls.in_flight += 1
        cls.max_in_flight = max(cls.max_in_flight, cls.in_flight)
        try:
            await asyncio.sleep(0.01)
            if cls.attempts <= cls.refusals:
                raise ConnectionRefusedError(111, 'Connection refused')
        finally:
            cls.in_flight -= 1

    async def getRawData(self, max_length=1024):
        await asyncio.sleep(10)

    async def closeNtripConnection(self):
        pass


@pytest.fixture
def fake_stream(monkeypatch):
    FakeStream.attempts = FakeStream.in_flight = FakeStream.max_in_flight = 0
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
