"""Rate limiter real do modo convidado (ADR-0003 §5): token bucket em memória.

Implementa o protocolo `RateLimiter` da ADR-0002 (`check(principal, request)`), chamado pelo `authorize` no passo 5.
Chave = (id do convidado | IP do anon, balde). O host nunca é limitado.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from karaoke.errors import rate_limited
from karaoke.security import MUTATING_METHODS, Principal


@dataclass(frozen=True)
class BucketSpec:
    capacity: float
    per_min: float

    @property
    def rate(self) -> float:  # tokens por segundo
        return self.per_min / 60.0


BUCKETS: dict[str, BucketSpec] = {
    "search": BucketSpec(10, 30),  # GET /api/search (YouTube; DECISION 0019: busca enquanto digita)
    "write": BucketSpec(10, 20),  # demais mutações do convidado
    "read": BucketSpec(60, 120),  # demais GETs do convidado
    "session": BucketSpec(
        10, 20
    ),  # POST /api/guest/session (por IP; DECISION 0016: folga p/ ~10 atrás de 1 NAT)
    "anon": BucketSpec(60, 120),  # rotas `public` sem sessão (por IP)
    "static": BucketSpec(300, 600),  # `/guest` e `/static/*` (por IP; review-1 L-4: não gastam `read`/`anon`)
}
SESSION_PATH = "/api/guest/session"
SEARCH_PATH = "/api/search"
GUEST_PAGE = "/guest"
STATIC_PREFIX = "/static/"
_PRUNE_AT = 2048


def is_static(method: str, path: str) -> bool:
    return method in ("GET", "HEAD") and (path == GUEST_PAGE or path.startswith(STATIC_PREFIX))


def bucket_for(principal: Principal, method: str, path: str) -> str:
    if method == "POST" and path == SESSION_PATH:
        return "session"
    if is_static(method, path):
        return "static"
    if principal.role != "guest":
        return "anon"
    if path == SEARCH_PATH:
        return "search"
    return "write" if method in MUTATING_METHODS else "read"


class TokenBucketLimiter:
    def __init__(
        self, buckets: dict[str, BucketSpec] | None = None, clock: Callable[[], float] = time.monotonic
    ):
        self.buckets = dict(buckets or BUCKETS)
        self._clock = clock
        self._state: dict[tuple[str, str], tuple[float, float]] = {}  # chave → (tokens, último instante)
        self._lock = threading.Lock()

    def take(self, key: str, bucket: str) -> float:
        """Consome 1 token. Devolve 0 se passou, ou os segundos até o próximo token."""
        spec = self.buckets[bucket]
        now = self._clock()
        with self._lock:
            tokens, last = self._state.get((key, bucket), (spec.capacity, now))
            tokens = min(spec.capacity, tokens + (now - last) * spec.rate)
            if tokens >= 1:
                self._state[(key, bucket)] = (tokens - 1, now)
                if len(self._state) > _PRUNE_AT:
                    self._prune(now)
                return 0.0
            self._state[(key, bucket)] = (tokens, now)
            return (1 - tokens) / spec.rate

    def _prune(self, now: float) -> None:
        """Esquece baldes que já estariam cheios (não mudam o resultado)."""
        for (key, bucket), (tokens, last) in list(self._state.items()):
            spec = self.buckets[bucket]
            if tokens + (now - last) * spec.rate >= spec.capacity:
                del self._state[(key, bucket)]

    def reset(self) -> None:
        with self._lock:
            self._state.clear()

    def check(self, principal: Principal, request: Any) -> None:
        if principal.role == "host":
            return
        bucket = bucket_for(principal, request.method, request.url.path)
        if principal.role == "guest" and bucket not in ("session", "static"):
            key = principal.id
        else:
            key = request.client.host if request.client else "?"
        wait = self.take(key, bucket)
        if wait > 0:
            raise rate_limited(math.ceil(wait), bucket)
