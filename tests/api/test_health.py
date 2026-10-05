from karaoke import __version__


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "version": __version__}
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_pages_use_tokens(client):
    for path in ("/", "/stage"):
        r = client.get(path)
        assert r.status_code == 200
        assert "/static/css/tokens.css" in r.text
    css = client.get("/static/css/tokens.css")
    assert css.status_code == 200
    assert "--color-accent" in css.text


def test_static_no_traversal(client):
    assert client.get("/static/../app.py").status_code == 404
    assert client.get("/static/%2e%2e/app.py").status_code == 404


def test_stub_returns_501_in_error_format(app):
    # T-005: /api/queue deixou de ser stub; o formato do 501 é testado com uma rota de teste.
    from fastapi import APIRouter
    from fastapi.testclient import TestClient

    from karaoke.errors import not_implemented
    from karaoke.security import scope
    from tests.conftest import BASE_URL

    router = APIRouter(dependencies=[scope("host")])

    @router.get("/api/_test/stub")
    async def _stub() -> None:
        raise not_implemented("test.stub")

    app.include_router(router)
    with TestClient(app, base_url=BASE_URL, client=("127.0.0.1", 50000)) as client:
        r = client.get("/api/_test/stub")
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "not_implemented"


def test_unknown_route_error_format(client):
    r = client.get("/api/nao-existe")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
