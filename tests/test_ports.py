"""Port auto-resolution: busy preferred ports slide to a free one."""

import socket

import pytest

from micast.config import port_in_use, resolve_port


@pytest.fixture
def taken_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_free_port_used_as_is():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert resolve_port(free, "MICAST_TEST_PORT") == free


def test_busy_port_slides_to_next_free(taken_port, monkeypatch):
    monkeypatch.delenv("MICAST_TEST_PORT", raising=False)
    resolved = resolve_port(taken_port, "MICAST_TEST_PORT")
    assert resolved != taken_port
    assert not port_in_use(resolved)


def test_pinned_port_fails_loudly(taken_port, monkeypatch):
    # Only real env vars (present before .env loading) count as pins.
    monkeypatch.setattr(
        "micast.config._ENV_PINNED", frozenset({"MICAST_TEST_PORT"})
    )
    with pytest.raises(RuntimeError, match="已被占用"):
        resolve_port(taken_port, "MICAST_TEST_PORT")


def test_port_in_use_detection(taken_port):
    assert port_in_use(taken_port)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert not port_in_use(free)


def test_raop_configure_ports_moves_scan_base(monkeypatch):
    from micast.raop import server as raop_server

    monkeypatch.setattr(raop_server, "_reserved_udp_bases", set())
    raop_server.configure_ports(5100, 6300)
    try:
        assert raop_server.rtsp_base() == 5100
        assert raop_server.udp_pool() == (6300, 6300 + 195)
        base = raop_server._reserve_udp_base()
        assert 6300 <= base <= 6495
    finally:
        raop_server.configure_ports(None, None)
    assert raop_server.rtsp_base() == 5000
    assert raop_server.udp_pool() == (6000, 6195)


def test_set_ports_validates_and_persists(tmp_path, monkeypatch):
    from micast.config import Settings

    monkeypatch.setenv("MICAST_DATA_DIR", str(tmp_path))
    s = Settings()
    s.set_ports({"airplay_rtsp_port": 5100, "stream_port": 18080})
    assert s.airplay_rtsp_port == 5100
    assert s.preferred_port("stream_port") == 18080

    import json

    saved = json.loads((tmp_path / "micast.json").read_text(encoding="utf-8"))
    assert saved["airplay_rtsp_port"] == 5100
    assert saved["stream_port"] == 18080

    s.set_ports({"airplay_rtsp_port": None})
    assert s.airplay_rtsp_port is None
    with pytest.raises(ValueError):
        s.set_ports({"stream_port": 80})
    with pytest.raises(ValueError):
        s.set_ports({"nonsense": 5000})


def test_resolved_port_not_persisted_as_preferred(tmp_path, monkeypatch):
    """A one-off slide (3000 busy) must not become the persisted preference."""
    from micast.config import Settings

    monkeypatch.setenv("MICAST_DATA_DIR", str(tmp_path))
    s = Settings()
    s.apply_resolved_port("port", 3007)
    assert s.port == 3007
    assert s.preferred_port("port") == 3000
    s.save_to_file()

    import json

    saved = json.loads((tmp_path / "micast.json").read_text(encoding="utf-8"))
    assert saved["port"] == 3000


def test_ports_report_pruned_by_deployment(monkeypatch):
    from micast.config import settings
    from micast.routes.config import _ports_report

    monkeypatch.setattr(settings, "airplay2_enabled", False)
    monkeypatch.delenv("MICAST_UNIX_SOCKET", raising=False)

    monkeypatch.setenv("MICAST_DEPLOYMENT", "windows")
    ids = {entry["id"] for entry in _ports_report(None, None)}
    assert "airplay2_port" not in ids
    assert {"port", "stream_port", "airplay_rtsp_port", "airplay_udp_base", "mdns", "ssdp"} <= ids
    web = next(e for e in _ports_report(None, None) if e["id"] == "port")
    assert web["editable"]

    monkeypatch.setenv("MICAST_DEPLOYMENT", "fnos")
    monkeypatch.setenv("MICAST_UNIX_SOCKET", "/run/micast/app.sock")
    entries = _ports_report(None, None)
    ids = {entry["id"] for entry in entries}
    assert {"airplay2_port", "nqptp"} <= ids
    web = next(e for e in entries if e["id"] == "port")
    assert web["status"] == "hosted" and not web["editable"]
    ap2 = next(e for e in entries if e["id"] == "airplay2_port")
    assert ap2["editable"]
