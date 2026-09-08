from __future__ import annotations

import pytest

from micast.config import ReceiverConfig, settings
from micast.orchestration import OrchestratedReceiver
from micast.receiver_manager import ReceiverManager


class FakeOrchestrator:
    configured = True

    def __init__(self, result: list[OrchestratedReceiver] | None = None):
        self.result = result or []
        self.desired = []

    async def reconcile(self, desired):
        self.desired = desired
        return self.result


@pytest.fixture(autouse=True)
def restore_settings():
    original = {
        "receiver_mode": settings.receiver_mode,
        "selected_device_id": settings.selected_device_id,
        "airplay_protocol": settings.airplay_protocol,
        "speakers": list(settings.speakers),
        "receivers": list(settings.receivers),
    }
    yield
    settings.receiver_mode = original["receiver_mode"]
    settings.selected_device_id = original["selected_device_id"]
    settings.airplay_protocol = original["airplay_protocol"]
    settings.speakers = original["speakers"]
    settings.receivers = original["receivers"]


@pytest.mark.asyncio
async def test_receiver_definition_is_reconciled():
    settings.receivers = [
        ReceiverConfig(id="main", name="MiCast", target_type="speaker", target_id="speaker-1")
    ]
    fake = FakeOrchestrator(
        [
            OrchestratedReceiver(
                key="main",
                device_id="main",
                name="MiCast",
                status="running",
                pcm_host="micast-receiver-single",
                pcm_port=9001,
            )
        ]
    )
    manager = ReceiverManager(orchestrator=fake)

    await manager.start()

    assert len(fake.desired) == 1
    assert fake.desired[0].device_id == "main"
    assert fake.desired[0].protocol == "airplay2"
    assert manager.receivers[0].status == "running"
    assert manager.orchestration_status["status"] == "running"


@pytest.mark.asyncio
async def test_only_enabled_receiver_definitions_are_reconciled():
    settings.receivers = [
        ReceiverConfig(id="living", name="客厅小爱", target_type="speaker", target_id="living"),
        ReceiverConfig(
            id="kitchen",
            name="厨房小爱",
            target_type="speaker",
            target_id="kitchen",
            enabled=False,
        ),
    ]
    fake = FakeOrchestrator()
    manager = ReceiverManager(orchestrator=fake)

    await manager.start()

    assert [item.device_id for item in fake.desired] == ["living"]
    assert fake.desired[0].name == "客厅小爱"


@pytest.mark.asyncio
async def test_multi_receiver_without_orchestrator_is_explicitly_unavailable():
    settings.receiver_mode = "multi"
    settings.receivers = [
        ReceiverConfig(id="living", name="客厅小爱", target_type="speaker", target_id="living")
    ]

    class UnconfiguredOrchestrator:
        configured = False

    manager = ReceiverManager(orchestrator=UnconfiguredOrchestrator())
    await manager.start()

    assert manager.receivers[0].status == "error"
    assert "Docker" in manager.receivers[0].detail
    assert manager.orchestration_status["status"] == "unconfigured"
