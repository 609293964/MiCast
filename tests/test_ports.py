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
