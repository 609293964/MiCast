import sys
from types import SimpleNamespace

from micast import __main__ as entrypoint


def test_unix_socket_mode_serves_dlna_over_tcp_alongside_uds(monkeypatch, tmp_path):
    socket_path = tmp_path / "micast.sock"
    fake_app = object()
    calls = []

    monkeypatch.setenv("MICAST_UNIX_SOCKET", str(socket_path))
    monkeypatch.setattr(entrypoint.settings, "port", 3000)
    monkeypatch.setattr(entrypoint, "resolve_port", lambda *_: 3456)
    monkeypatch.setattr(
        entrypoint,
        "_run_with_unix_socket",
        lambda app, path: calls.append((app, path)),
    )
    monkeypatch.setitem(sys.modules, "micast.main", SimpleNamespace(app=fake_app))

    entrypoint.main()

    assert calls == [(fake_app, socket_path)]
    # The TCP port is resolved so the SSDP-advertised DLNA URL is reachable.
    assert entrypoint.settings.port == 3456


def test_tcp_mode_keeps_host_and_resolved_port(monkeypatch):
    fake_app = object()
    calls = []

    monkeypatch.delenv("MICAST_UNIX_SOCKET", raising=False)
    monkeypatch.setattr(entrypoint.settings, "port", 3000)
    monkeypatch.setattr(entrypoint, "resolve_port", lambda *_: 3456)
    monkeypatch.setattr(
        entrypoint.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs))
    )
    monkeypatch.setitem(sys.modules, "micast.main", SimpleNamespace(app=fake_app))

    entrypoint.main()

    assert calls == [
        (fake_app, {"host": entrypoint.settings.host, "port": 3456, "log_level": "info"})
    ]


def test_run_with_unix_socket_binds_uds_and_dlna_tcp(monkeypatch, tmp_path):
    socket_path = tmp_path / "micast.sock"
    fake_app = object()
    served = []

    class FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            served.append(self.config)

    monkeypatch.setattr(entrypoint.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(entrypoint.settings, "host", "0.0.0.0")
    monkeypatch.setattr(entrypoint.settings, "port", 3456)

    entrypoint._run_with_unix_socket(fake_app, socket_path)

    assert len(served) == 2
    assert served[0].uds == str(socket_path)
    assert served[1].host == "0.0.0.0" and served[1].port == 3456
