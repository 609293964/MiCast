from fastapi.testclient import TestClient

from micast.main import app


def test_brand_assets_are_served_by_the_app():
    client = TestClient(app)
    expected_types = {
        "/icons/micast.svg": "image/svg+xml",
        "/icons/favicon.ico": "image/x-icon",
        "/icons/apple-touch-icon.png": "image/png",
        "/icons/icon-192.png": "image/png",
        "/icons/icon-512.png": "image/png",
        "/icons/maskable-192.png": "image/png",
        "/icons/maskable-512.png": "image/png",
        "/site.webmanifest": "application/manifest+json",
    }

    for path, media_type in expected_types.items():
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(media_type), path
        assert response.content, path


def test_canonical_app_path_serves_ui_api_and_assets():
    client = TestClient(app)

    # Reverse proxies such as the fnOS Unix-socket gateway strip the public
    # /app/micast prefix before forwarding the request.
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 200
    assert '<base href="/app/micast/"' in root.text

    page = client.get("/app/micast/")
    assert page.status_code == 200
    assert '<base href="/app/micast/"' in page.text

    assert client.get("/app/micast/health").json() == {"status": "ok"}
    assert client.get("/app/micast/icons/micast.svg").status_code == 200
    assert client.get("/app/micast/site.webmanifest").status_code == 200
