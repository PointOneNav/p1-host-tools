from datetime import datetime, timedelta, timezone

from p1_runner.runner import P1Runner


class FakeNtripClient:
    def __init__(self, connected=False):
        self.connected = connected
        self.positions = []

    def is_connected(self):
        return self.connected

    def send_position(self, lla_deg):
        self.positions.append(list(lla_deg))
        return self.connected


def _runner():
    """A P1Runner with only the NTRIP state, so no serial port is opened."""
    runner = P1Runner.__new__(P1Runner)
    runner.ntrip_client = None
    runner.ntrip_aux_client = None
    runner.ntrip_position_override = None
    runner.last_ntrip_position_update = None
    return runner


def test_override_set_before_the_client_exists_reaches_it(monkeypatch):
    runner = _runner()
    runner.set_ntrip_position_override([48.88, 2.38, 120.0])
    created = FakeNtripClient()
    monkeypatch.setattr('p1_runner.runner.NTRIPClient', lambda **kwargs: created)
    monkeypatch.setattr(P1Runner, 'is_alive', lambda self: False)
    runner.connect_to_ntrip(url='https://polaris.pointonenav.com:2102', mountpoint='POLARIS', username='u',
                            password='p')
    assert created.positions == [[48.88, 2.38, 120.0]]


def test_override_is_resent_every_60_seconds_without_fusionengine_poses():
    runner = _runner()
    runner.ntrip_client = FakeNtripClient(connected=True)
    runner.ntrip_position_override = [48.88, 2.38, 120.0]
    runner._send_ntrip_position_update()
    runner._send_ntrip_position_update()
    assert len(runner.ntrip_client.positions) == 1
    runner.last_ntrip_position_update = datetime.now(tz=timezone.utc) - timedelta(seconds=61)
    runner._send_ntrip_position_update()
    assert len(runner.ntrip_client.positions) == 2
