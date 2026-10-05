"""Regras de convidado que dependem de dados (ADR-0003 §4–§6, api.md §0.1 ¹–⁵ e §12). Dono: backend-engineer (T-004).

O `authorize` decide escopo × papel; aqui ficam as restrições que só os handlers conseguem checar (duração
apurada pelo servidor, posse de job, item tocando). Não há cotas por convidado (DECISION 0016). **Toda função é no-op para o host**: o fluxo da v0.2 não muda.

Os códigos de erro novos (§0 "Formato de erro") são criados aqui com `ApiError(...)` direto, para não mexer em
`karaoke/errors.py` (dono: T-003). O orchestrator pode unificar depois.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any

from karaoke.errors import ApiError, invalid_request

NAME_MAX = 40
_STRIP_CATEGORIES = ("Cc", "Cf")  # controle e formatação (inclui overrides bidi)


# ---- papel -------------------------------------------------------------------------------------------
def is_guest(principal: Any) -> bool:
    return getattr(principal, "role", None) == "guest"


# ---- nome / cantor -----------------------------------------------------------------------------------
def clean_text(value: str | None) -> str:
    """NFC, sem caracteres `Cc`/`Cf`, espaços colapsados e `strip()`."""
    if not value:
        return ""
    value = unicodedata.normalize("NFC", value)
    value = "".join(" " if ch.isspace() else ch for ch in value)
    value = "".join(ch for ch in value if unicodedata.category(ch) not in _STRIP_CATEGORIES)
    return " ".join(value.split())


def sanitize_name(name: str | None, field: str = "name") -> str:
    """Nome de convidado: 1–40 caracteres após o saneamento; senão `400 invalid_request {fields:{<field>}}`."""
    clean = clean_text(name)
    if not clean:
        raise invalid_request("Informe um nome.", {field: "obrigatório"})
    if len(clean) > NAME_MAX:
        raise invalid_request(
            f"Use no máximo {NAME_MAX} caracteres.", {field: f"máximo {NAME_MAX} caracteres"}
        )
    return clean


def guest_name(principal: Any) -> str | None:
    """Nome da sessão, saneado (o A1 já saneia na criação; aqui é defesa em profundidade)."""
    clean = clean_text(getattr(principal, "name", None))
    return clean[:NAME_MAX] or None


def guest_singer(principal: Any, singer: str | None) -> str | None:
    """`singer` do convidado: padrão = nome da sessão; se informado, saneado e ≤ 40."""
    if singer is not None and clean_text(singer):
        return sanitize_name(singer, "singer")
    return guest_name(principal)


def ensure_position_end(principal: Any, position: Any) -> None:
    """Convidado só adiciona ao fim da fila (§0.1 ⁴)."""
    if is_guest(principal) and position not in (None, "end"):
        raise invalid_request("Pedidos entram no fim da fila.", {"position": "só 'end'"})


# ---- erros (§0, v0.3) -----------------------------------------------------------------------------------
def long_video_not_allowed(duration_ms: int, max_ms: int) -> ApiError:
    minutes = max(1, round(max_ms / 60000))
    return ApiError(
        409,
        "long_video_not_allowed",
        f"Vídeos com mais de {minutes} minutos não podem ser pedidos.",
        {"duration_ms": int(duration_ms), "max_ms": int(max_ms)},
    )


def queue_item_playing(item_id: str) -> ApiError:
    return ApiError(
        409,
        "queue_item_playing",
        "Essa música já está tocando e não pode mais ser removida.",
        {"queue_item_id": item_id},
    )


def song_queued_by_others(song_id: str, count: int) -> ApiError:
    return ApiError(
        409,
        "song_queued_by_others",
        "Há pedidos de convidados com esta música na fila. Remova-os antes de excluir a música.",
        {"song_id": song_id, "count": int(count)},
    )


# ---- duração ----------------------------------------------------------------------------------------------
def ensure_duration(principal: Any, duration_ms: int | None, max_ms: int) -> None:
    """Duração **apurada pelo servidor** > limite → `409 long_video_not_allowed` (sem confirmação)."""
    if is_guest(principal) and duration_ms is not None and duration_ms > max_ms:
        raise long_video_not_allowed(duration_ms, max_ms)


# ---- fila: remoção -------------------------------------------------------------------------------------------
def ensure_guest_can_remove_item(principal: Any, row: Any, item_id: str) -> None:
    """Depois da posse: convidado só remove itens `pending` (o tocando → `409 queue_item_playing`)."""
    if not is_guest(principal):
        return
    status = row["status"]
    if status == "playing":
        raise queue_item_playing(item_id)
    if status != "pending":
        raise invalid_request("Só pedidos que ainda estão na fila podem ser removidos.", {"item_id": status})


# ---- jobs ------------------------------------------------------------------------------------------------------
def job_visible_to(principal: Any, job: Mapping[str, Any] | Any) -> bool:
    """Host vê todos; convidado só os que pediu (`requested_by == principal.id`)."""
    if not is_guest(principal):
        return True
    if job is None:
        return False
    try:
        owner = job["requested_by"]
    except (KeyError, TypeError, IndexError):
        owner = getattr(job, "requested_by", None)
    return owner is not None and owner == principal.id
