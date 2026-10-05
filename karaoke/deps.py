"""Dependências compartilhadas pelos routers (acesso ao estado do app e validadores de parâmetros).

Dono: architect. Os routers importam daqui; não editam app.py.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path, Request

from karaoke.config import Config
from karaoke.db import Database
from karaoke.events import EventBus

SONG_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
JOB_ID_PATTERN = r"^j_[0-9]+$"
QUEUE_ITEM_ID_PATTERN = r"^q_[0-9]+$"

SongId = Annotated[str, Path(pattern=SONG_ID_PATTERN)]
JobId = Annotated[str, Path(pattern=JOB_ID_PATTERN)]
QueueItemId = Annotated[str, Path(pattern=QUEUE_ITEM_ID_PATTERN)]


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus
