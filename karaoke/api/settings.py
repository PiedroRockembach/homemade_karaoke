"""Configurações do usuário — contrato §3 (`/api/settings`). Dono: backend-engineer (T-005).

Também expõe as funções de leitura/gravação usadas por outros módulos (player; T-006 pode ler
`separation_model`, `whisper_model` e `long_video_warn_s` com `get_settings(db, config)`):
- `get_settings(db, config)` → objeto completo (padrões + valores salvos na tabela `settings`).
- `update_settings(app, patch)` → grava, publica `settings.updated` e sincroniza o player (volume/autoplay/voz guia).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from karaoke.config import Config
from karaoke.db import Database
from karaoke.errors import invalid_request
from karaoke.security import scope

router = APIRouter(tags=["settings"], dependencies=[scope("host")])

WHISPER_MODELS = ("large-v3-turbo", "medium", "small")


def default_settings(config: Config) -> dict[str, Any]:
    whisper = config.whisper_model if config.whisper_model in WHISPER_MODELS else "large-v3-turbo"
    return {
        "countdown_s": 8,
        "autoplay": True,
        "guide_vocal_default": 0.0,
        "volume": 0.8,
        "separation_model": config.separation_model,
        "whisper_model": whisper,
        "long_video_warn_s": 900,
        "theme": "system",
        "lyrics_mode": "aligned",
    }


def read_settings(conn: sqlite3.Connection, config: Config) -> dict[str, Any]:
    out = default_settings(config)
    for row in conn.execute("SELECT key, value_json FROM settings"):
        if row["key"] in out:
            out[row["key"]] = json.loads(row["value_json"])
    if out["lyrics_mode"] not in ("aligned", "online"):  # `transcribe` = alias legado de `aligned` (CR-001)
        out["lyrics_mode"] = "aligned"
    return out


def get_settings(db: Database, config: Config) -> dict[str, Any]:
    conn = db.connect()
    try:
        return read_settings(conn, config)
    finally:
        conn.close()


def write_settings(conn: sqlite3.Connection, patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        conn.execute(
            "INSERT INTO settings(key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key, json.dumps(value)),
        )


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countdown_s: int | None = Field(default=None, ge=0, le=30)
    autoplay: bool | None = None
    guide_vocal_default: float | None = Field(default=None, ge=0.0, le=1.0)
    volume: float | None = Field(default=None, ge=0.0, le=1.0)
    separation_model: Literal["htdemucs_ft", "htdemucs"] | None = None
    whisper_model: Literal["large-v3-turbo", "medium", "small"] | None = None
    long_video_warn_s: int | None = Field(default=None, ge=60, le=86400)
    theme: Literal["system", "light", "dark"] | None = None
    lyrics_mode: Literal["aligned", "transcribe", "online"] | None = None  # transcribe → gravado como aligned


def update_settings(app: FastAPI, patch: dict[str, Any]) -> dict[str, Any]:
    """Grava `patch` (já validado), publica `settings.updated` e reflete volume/autoplay/voz guia no player."""
    from karaoke.services.library import transaction

    config: Config = app.state.config
    if patch.get("lyrics_mode") == "transcribe":
        patch = {**patch, "lyrics_mode": "aligned"}
    with transaction(app) as txn:
        write_settings(txn.conn, patch)
        player = getattr(app.state, "player", None)
        if player is not None:
            player.apply_settings(txn, patch)
        settings = read_settings(txn.conn, config)
        txn.emit("settings.updated", settings)
    return settings


@router.get("/api/settings")
def get_settings_route(request: Request) -> dict:
    return get_settings(request.app.state.db, request.app.state.config)


@router.patch("/api/settings")
def patch_settings(body: SettingsPatch, request: Request) -> dict:
    patch = body.model_dump(exclude_unset=True)
    nulls = [k for k, v in patch.items() if v is None]
    if nulls:
        raise invalid_request("Valores nulos não são aceitos.", dict.fromkeys(nulls, "não pode ser null"))
    if not patch:
        return get_settings(request.app.state.db, request.app.state.config)
    return update_settings(request.app, patch)
