"""Packaging-readiness: data dir resolution and QR decoding (zxing-cpp)."""

from io import BytesIO
from pathlib import Path

import qrcode
import zxingcpp
from PIL import Image

from micast import config


def test_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MICAST_DATA_DIR", str(tmp_path / "data"))
    assert config.default_data_dir() == tmp_path / "data"


def test_data_dir_prefers_repo_config_in_checkout(monkeypatch):
    monkeypatch.delenv("MICAST_DATA_DIR", raising=False)
    # This test itself runs from a source checkout with config/ present.
    assert config.default_data_dir().name == "config"
    assert (config.default_data_dir().parent / "micast").is_dir()


def test_data_dir_packaged_falls_back_to_user_dir(monkeypatch):
    monkeypatch.delenv("MICAST_DATA_DIR", raising=False)
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/Users/tester")))
    assert config.default_data_dir() == Path(
        "/Users/tester/Library/Application Support/MiCast"
    )


def test_zxing_decodes_generated_qr():
    img = qrcode.make("https://account.xiaomi.com/long/abc123").convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    results = zxingcpp.read_barcodes(Image.open(buf))
    assert results
    assert results[0].text == "https://account.xiaomi.com/long/abc123"
