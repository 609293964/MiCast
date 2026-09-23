import asyncio

from micast.xiaomi.device_manager import DeviceManager


class RotatingAuth:
    def __init__(self):
        self.service = object()

    async def ensure_service(self):
        return self.service


async def test_device_manager_adopts_rotated_auth_service():
    auth = RotatingAuth()
    manager = DeviceManager(auth)

    assert await manager.refresh_service()
    first = manager._service
    auth.service = object()
    assert await manager.refresh_service()

    assert manager._service is auth.service
    assert manager._service is not first


async def test_close_cancels_and_joins_all_owned_tasks():
    manager = DeviceManager(RotatingAuth())

    async def forever():
        await asyncio.Event().wait()

    watchdog = asyncio.create_task(forever())
    retry = asyncio.create_task(forever())
    manager._watchdog_tasks["speaker"] = watchdog
    manager._error_retry_task = retry

    await manager.close()

    assert watchdog.cancelled()
    assert retry.cancelled()
    assert not manager._watchdog_tasks
    assert manager._error_retry_task is None
