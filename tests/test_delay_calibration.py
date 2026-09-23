import httpx
import pytest

from micast.routes.debug import _request_offsets
from micast.stream_server import StreamServer


def test_request_offsets_preserve_observed_order_without_claiming_audible_direction():
    arrivals = {"anchor": 100.0, "early": 99.8, "late": 100.35}

    assert _request_offsets(arrivals, "anchor", ["anchor", "early", "late"]) == {
        "early": -200,
        "late": 350,
    }


def test_request_offsets_round_to_control_step_and_clamp_outliers():
    arrivals = {"anchor": 100.0, "near": 100.024, "outlier": 130.0}

    assert _request_offsets(arrivals, "anchor", ["anchor", "near", "outlier"]) == {"outlier": 20000}


@pytest.mark.asyncio
async def test_speaker_facing_calibration_path_records_sink_without_query_string():
    server = StreamServer()
    session = server.begin_delay_calibration("run-1", ["speaker-a", "speaker-b"])
    transport = httpx.ASGITransport(app=server._app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/calibration/run-1/speaker-a.wav")
        second = await client.get("/calibration/run-1/speaker-b.wav")

    assert first.status_code == 200
    assert first.headers["content-type"].startswith("audio/wav")
    assert second.status_code == 200
    assert set(session["arrivals"]) == {"speaker-a", "speaker-b"}
    assert session["ready"].is_set()
