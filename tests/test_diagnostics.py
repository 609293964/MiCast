"""Sanitizer + SSRF guard coverage for the diagnostic report feature."""

import pytest

from micast.diagnostics import sanitize_obj, sanitize_text
from micast.url_safety import validate_http_url


def test_sanitize_redacts_credential_pairs():
    text = "login serviceToken=abc123 passToken: tok_789 ssecurity=hexdead"
    out = sanitize_text(text)
    assert "abc123" not in out and "tok_789" not in out and "hexdead" not in out


def test_sanitize_redacts_cookie_and_webhook_tokens():
    # The cookie scrub intentionally masks to end of line: cookie values
    # contain spaces, so a partial mask would leak the tail pairs.
    assert sanitize_text('cookie: a=1; passToken=xyz') == "cookie: ***"
    out = sanitize_text("GET https://open.feishu.cn/open-apis/bot/v2/hook/deadbeefcafe 200")
    assert "deadbeefcafe" not in out
    assert "open.feishu.cn" in out  # host stays readable


def test_sanitize_obj_scrubs_nested_strings():
    obj = {"outer": {"token": "AT_1234567890ab"}, "items": ["uid=UID_abcdef12"]}
    out = sanitize_obj(obj)
    assert "AT_1234567890ab" not in str(out)
    assert "UID_abcdef12" not in str(out)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x.mp3",
        "http://localhost/a",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0/",
        "http://[::1]/x",
        "http://224.0.0.1/",
        "ftp://example.com/x",
        "not-a-url",
    ],
)
@pytest.mark.asyncio
async def test_validate_http_url_rejects(url):
    with pytest.raises(ValueError):
        await validate_http_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10/music/a.mp3",  # LAN NAS is a legitimate target
        "http://10.0.0.5:8000/a.flac",
    ],
)
@pytest.mark.asyncio
async def test_validate_http_url_allows_lan(url):
    assert await validate_http_url(url) == url
