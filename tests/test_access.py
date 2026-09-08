import json

from micast.access import AccessManager


def test_first_run_is_unconfigured(tmp_path):
    manager = AccessManager(tmp_path / "access.json")
    assert not manager.access_configured
    assert not manager.setup_complete
    assert not manager.auth_enabled


def test_protected_setup_hashes_password_and_issues_session(tmp_path):
    path = tmp_path / "access.json"
    manager = AccessManager(path)
    manager.configure(enabled=True, username="admin", password="secret12")
    manager.complete_setup()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["password_hash"] != "secret12"
    assert manager.verify_password("admin", "secret12")
    assert not manager.verify_password("admin", "wrong")
    assert manager.valid_session(manager.issue_session())


def test_changing_access_invalidates_old_session(tmp_path):
    manager = AccessManager(tmp_path / "access.json")
    manager.configure(enabled=True, password="secret12")
    old = manager.issue_session()
    manager.configure(enabled=True, password="newsecret")
    assert not manager.valid_session(old)


def test_open_access_needs_no_session(tmp_path):
    manager = AccessManager(tmp_path / "access.json")
    manager.configure(enabled=False)
    manager.complete_setup()
    assert manager.valid_session(None)
