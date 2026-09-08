import pytest
from pydantic import ValidationError

from micast.orchestrator_app import ReceiverSpec, _container_name, _spec_hash


def test_receiver_contract_rejects_shell_sensitive_names():
    with pytest.raises(ValidationError):
        ReceiverSpec(key="single", device_id="speaker-1", name='bad " name')


def test_container_name_and_spec_hash_are_stable():
    spec = ReceiverSpec(
        key="speaker-device-123",
        device_id="device-123",
        name="厨房小爱",
        protocol="auto",
    )

    assert _container_name(spec.key) == _container_name(spec.key)
    assert _container_name(spec.key).startswith("micast-receiver-")
    assert _spec_hash(spec) == _spec_hash(spec)
