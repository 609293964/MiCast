import asyncio
import logging

from micast.runtime_log import RuntimeLogHandler, install_asyncio_exception_filter


def test_runtime_log_is_bounded():
    handler = RuntimeLogHandler(capacity=3)
    logger = logging.getLogger("test.runtime.bounded")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        for index in range(5):
            logger.info("record-%s", index)
        assert [item["message"] for item in handler.snapshot()] == [
            "record-2",
            "record-3",
            "record-4",
        ]
    finally:
        logger.removeHandler(handler)


def test_asyncio_filter_suppresses_windows_connection_reset(monkeypatch):
    loop = asyncio.new_event_loop()
    delegated = []
    monkeypatch.setattr(loop, "default_exception_handler", delegated.append)
    install_asyncio_exception_filter(loop)
    error = ConnectionResetError(10054, "connection reset")
    error.winerror = 10054
    loop.call_exception_handler({"exception": error})
    assert delegated == []
    loop.close()


def test_asyncio_filter_keeps_other_errors(monkeypatch):
    loop = asyncio.new_event_loop()
    delegated = []
    monkeypatch.setattr(loop, "default_exception_handler", delegated.append)
    install_asyncio_exception_filter(loop)
    context = {"exception": RuntimeError("boom")}
    loop.call_exception_handler(context)
    assert delegated == [context]
    loop.close()
