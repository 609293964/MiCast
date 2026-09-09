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
