"""Rate limiter do modo convidado (ADR-0003 §5): token bucket, baldes e 429 com Retry-After."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from karaoke.errors import ApiError
from karaoke.guest.ratelimit import TokenBucketLimiter, bucket_for
from karaoke.security import ANON, HOST, Principal

GUEST = Principal(role="guest", id="g_1", name="Bia")
OTHER = Principal(role="guest", id="g_2", name="Caio")


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def req(method: str, path: str, ip: str = "192.168.1.50"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), client=SimpleNamespace(host=ip))


@pytest.mark.parametrize(
    ("principal", "method", "path", "bucket"),
    [
        (GUEST, "GET", "/api/search", "search"),
        (GUEST, "GET", "/api/songs", "read"),
        (GUEST, "GET", "/api/thumbs/abc", "read"),
        (GUEST, "POST", "/api/queue", "write"),
        (GUEST, "DELETE", "/api/queue/q_1", "write"),
        (GUEST, "POST", "/api/guest/session", "session"),
        (ANON, "POST", "/api/guest/session", "session"),
        (ANON, "GET", "/guest", "static"),  # review-1 L-4: estáticos têm balde próprio
        (ANON, "GET", "/static/css/guest.css", "static"),
        (GUEST, "GET", "/static/js/guest/main.js", "static"),
        (ANON, "GET", "/api/guest/session", "anon"),
    ],
)
def test_bucket_for(principal, method, path, bucket):
    assert bucket_for(principal, method, path) == bucket


def test_host_never_limited():
    lim = TokenBucketLimiter(clock=Clock())
    for _ in range(500):
        lim.check(HOST, req("GET", "/api/search", "127.0.0.1"))


def test_search_bucket_burst_10_then_refill_one_per_2s():
    clock = Clock()
    lim = TokenBucketLimiter(clock=clock)
    for _ in range(10):
        lim.check(GUEST, req("GET", "/api/search"))
    with pytest.raises(ApiError) as exc:
        lim.check(GUEST, req("GET", "/api/search"))
    err = exc.value
    assert err.status == 429 and err.code == "rate_limited"
    assert err.details == {"retry_after_s": 2, "reason": "search"}  # 30/min → 1 token a cada 2 s
    assert err.headers == {"Retry-After": "2"}
    clock.t += 2
    lim.check(GUEST, req("GET", "/api/search"))  # 1 token de volta
    with pytest.raises(ApiError):
        lim.check(GUEST, req("GET", "/api/search"))
    # outro convidado tem o próprio balde; e o balde `read` do mesmo convidado é independente
    lim.check(OTHER, req("GET", "/api/search"))
    lim.check(GUEST, req("GET", "/api/songs"))


def test_write_bucket_capacity_10():
    lim = TokenBucketLimiter(clock=Clock())
    for _ in range(10):
        lim.check(GUEST, req("POST", "/api/queue"))
    with pytest.raises(ApiError) as exc:
        lim.check(GUEST, req("POST", "/api/queue"))
    assert exc.value.details["reason"] == "write" and exc.value.details["retry_after_s"] == 3


def test_session_bucket_is_per_ip_even_for_guests():
    lim = TokenBucketLimiter(clock=Clock())
    for p in (ANON, GUEST, OTHER, ANON, GUEST) * 2:  # capacidade 10 (DECISION 0016)
        lim.check(p, req("POST", "/api/guest/session", "192.168.1.60"))
    with pytest.raises(ApiError) as exc:
        lim.check(ANON, req("POST", "/api/guest/session", "192.168.1.60"))
    assert exc.value.details["reason"] == "session"
    lim.check(ANON, req("POST", "/api/guest/session", "192.168.1.61"))  # outro IP


def test_retry_after_is_integer_at_least_one():
    clock = Clock()
    lim = TokenBucketLimiter(clock=clock)
    for _ in range(60):
        lim.check(ANON, req("GET", "/api/guest/session"))
    clock.t += 0.49  # quase 1 token (120/min = 2/s)
    with pytest.raises(ApiError) as exc:
        lim.check(ANON, req("GET", "/api/guest/session"))
    assert exc.value.details["retry_after_s"] == 1


# ---- review-1 L-4: carregar a página do convidado não gasta `read`/`anon` -------------------------------------------
def test_static_does_not_consume_read_or_anon():
    lim = TokenBucketLimiter(clock=Clock())
    for _ in range(140):  # ~14 recargas de ~20 arquivos (mesmo IP: anon antes da sessão, guest depois)
        lim.check(ANON, req("GET", "/static/js/guest/main.js"))
        lim.check(GUEST, req("GET", "/static/css/guest.css"))
    for _ in range(60):  # os baldes `anon` e `read` continuam cheios
        lim.check(ANON, req("GET", "/api/guest/session"))
        lim.check(GUEST, req("GET", "/api/songs"))
