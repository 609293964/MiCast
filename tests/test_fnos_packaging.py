import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FNOS = ROOT / "packaging" / "fnos"


def _manifest() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (FNOS / "manifest").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )


def test_fnos_package_declares_gateway_and_python_runtime():
    manifest = _manifest()
    entry = json.loads((FNOS / "app" / "ui" / "config").read_text(encoding="utf-8"))
    route = entry[".url"]["micast.main"]

    assert manifest["appname"] == "micast"
    assert manifest["install_dep_apps"] == "python312"
    assert manifest["maintainer"] == "DyMode"
    assert route["gatewayPrefix"] == "/app/micast"
    assert route["gatewaySocket"] == "app.sock"
    assert route["url"] == "/app/micast"


def test_fnos_package_has_every_required_lifecycle_file():
    required = {
        "main",
        "install_init",
        "install_callback",
        "upgrade_init",
        "upgrade_callback",
        "uninstall_init",
        "uninstall_callback",
        "config_init",
        "config_callback",
    }

    assert required <= {path.name for path in (FNOS / "cmd").iterdir()}
    assert json.loads((FNOS / "config" / "privilege").read_text(encoding="utf-8"))
    assert json.loads((FNOS / "config" / "resource").read_text(encoding="utf-8")) == {}
    uninstall_wizard = json.loads((FNOS / "wizard" / "uninstall").read_text(encoding="utf-8"))
    policy = uninstall_wizard[0]["items"][0]
    assert policy["field"] == "wizard_data_policy"
    assert policy["initValue"] == "keep_config"


def test_fnos_runtime_uses_installed_target_layout():
    main = (FNOS / "cmd" / "main").read_text(encoding="utf-8")
    install = (FNOS / "cmd" / "install_callback").read_text(encoding="utf-8")

    assert '${TRIM_APPDEST}/vendor:${TRIM_APPDEST}' in main
    assert 'MICAST_UNIX_SOCKET="$SOCKET_FILE"' in main
    assert 'cd "$TRIM_APPDEST"' in main
    assert '${TRIM_APPDEST}/vendor' in install
    assert "${TRIM_APPDEST}/app/vendor" not in main + install


def test_fnos_keeps_classic_airplay_and_starts_single_airplay2_on_demand():
    main = (FNOS / "cmd" / "main").read_text(encoding="utf-8")
    receiver = (
        FNOS / "app" / "airplay2-runtime" / "run-shairport"
    ).read_text(encoding="utf-8")

    assert 'MICAST_AIRPLAY_ENGINE="local"' in main
    assert 'MICAST_AIRPLAY_PROTOCOL="classic"' in main
    assert 'MICAST_AIRPLAY2_MODE="single"' in main
    assert 'MICAST_AIRPLAY2_PCM_SOURCE="local:' in main
    assert 'runtime/bin/nqptp' not in main
    assert 'runtime/bin/nqptp' in receiver
    assert 'service_type = "airplay2"' in receiver


def test_fnos_lifecycle_handles_health_upgrade_and_uninstall_policies():
    main = (FNOS / "cmd" / "main").read_text(encoding="utf-8")
    upgrade = (FNOS / "cmd" / "upgrade_init").read_text(encoding="utf-8")
    uninstall = (FNOS / "cmd" / "uninstall_callback").read_text(encoding="utf-8")

    assert "GET /health" in main
    assert "5242880" in main
    assert "upgrade-backup.tgz" in upgrade
    assert 'find "$TRIM_PKGVAR" -mindepth 1 -maxdepth 1' in uninstall
    assert '[ "$TRIM_PKGVAR" != "/" ]' in uninstall
    assert "keep_all)" in uninstall
    assert "remove_all)" in uninstall
    assert "! -name micast.json" in uninstall
