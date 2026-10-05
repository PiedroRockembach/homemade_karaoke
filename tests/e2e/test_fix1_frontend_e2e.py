"""Regressões da rodada FIX-1 no frontend (FIX-003). Dono: frontend-engineer.

- review-1 M3: Palco recarregado (F5) continua dono do áudio, mas sem gesto → pausa no servidor preservando a
  posição, mostra "Pressione Enter" e, após o Enter, retoma do mesmo ponto.
- review-1 H1 (lado Palco): assumir o áudio num 2º Palco não reinicia a música do zero.
- QA-1 M-03: botão Tocar/Pausar com anel de foco visível nos dois temas.
(QA-1 M-02 é coberto por `test_control_e2e.py::test_control_sliders_reflect_server_state_on_load`.)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from .conftest import Live
from .test_flow_e2e import LRC, SONG_1, open_control, wait_scene

pytestmark = pytest.mark.e2e

IS_MIRROR = "() => document.getElementById('stage').classList.contains('mirror')"


@pytest.fixture
def new_stage(live: Live, browser: Any) -> Iterator[Callable[[], Any]]:
    """Abre um Palco num contexto próprio e o fecha no fim (uma aba vazada reconectaria a outro servidor)."""
    contexts: list[Any] = []

    def make() -> Any:
        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        contexts.append(ctx)
        page = ctx.new_page()
        page.goto(f"{live.base}/stage")
        return page

    yield make
    for ctx in contexts:
        ctx.close()


def _player_when(live: Live, pred: Callable[[dict], bool], msg: str, timeout: float = 8.0) -> dict:
    return live.wait(lambda: (lambda p: p if pred(p) else None)(live.player()), timeout=timeout, msg=msg)


def _start_playing(live: Live, page: Any, min_ms: int) -> int:
    wait_scene(page, "start")
    page.keyboard.press("Enter")
    live.wait(lambda: live.player()["stage"]["audio_owner"], msg="claim")
    live.enqueue(SONG_1, "Ana")
    wait_scene(page, "play", timeout=15)
    p = _player_when(
        live,
        lambda p: p["status"] == "playing" and p["position_ms"] >= min_ms,
        f"tocando além de {min_ms} ms",
        timeout=15,
    )
    return p["position_ms"]


def test_stage_reload_pauses_without_gesture_and_resumes_from_position(live: Live, new_stage) -> None:
    live.seed_song(SONG_1, "Canção Um", seconds=40, lrc=LRC)
    stage = new_stage()
    _start_playing(live, stage, 5000)
    owner = live.player()["stage"]["audio_owner"]

    stage.reload()
    wait_scene(stage, "start")
    assert "Pressione" in stage.inner_text(".scene.on")
    paused = _player_when(live, lambda p: p["status"] == "paused", "servidor pausado à espera do gesto")
    assert paused["stage"]["audio_owner"] == owner, "o F5 mantém a posse (client_id do sessionStorage)"
    held = paused["position_ms"]
    assert held >= 4500, f"a posição deve ser preservada ao pausar (got {held})"

    stage.keyboard.press("Enter")
    wait_scene(stage, "play", timeout=15)
    _player_when(live, lambda p: p["status"] == "playing", "retomou após o Enter")
    # ticks de 500 ms trazem a posição real do motor de áudio: tem de seguir de onde parou, não de 0.
    resumed = _player_when(
        live, lambda p: p["position_ms"] > held + 300, "posição avança do ponto pausado", 6
    )
    assert resumed["position_ms"] < held + 5000, resumed["position_ms"]


def test_stage_reload_keeps_user_pause(live: Live, new_stage) -> None:
    """Se a música já estava pausada pelo usuário, o Enter após o F5 não a faz tocar sozinha."""
    live.seed_song(SONG_1, "Canção Um", seconds=40, lrc=LRC)
    stage = new_stage()
    _start_playing(live, stage, 2000)
    assert live.command("pause").status_code == 200
    stage.reload()
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    wait_scene(stage, "play", timeout=15)
    stage.wait_for_timeout(1500)
    assert live.player()["status"] == "paused"


def test_claim_on_second_stage_keeps_position(live: Live, new_stage) -> None:
    live.seed_song(SONG_1, "Canção Um", seconds=40, lrc=LRC)
    s1 = new_stage()
    before = _start_playing(live, s1, 5000)
    s2 = new_stage()
    s2.wait_for_function(IS_MIRROR, timeout=8000)
    s2.keyboard.press("Enter")
    s2.wait_for_function(f"() => !({IS_MIRROR})()", timeout=5000)
    s2.wait_for_timeout(1500)  # carregou, reportou `loaded` e já mandou ticks
    p = live.player()
    assert p["status"] == "playing"
    assert p["position_ms"] >= before, f"transferir o som reiniciou a música ({p['position_ms']} < {before})"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_play_button_has_visible_focus_ring(live: Live, context, theme: str) -> None:
    live.seed_song(SONG_1, "Canção Um", seconds=20, lrc=LRC)
    live.enqueue(SONG_1, "Ana")
    page = open_control(context, live)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)
    page.wait_for_function("() => !document.getElementById('btn-play').disabled", timeout=8000)
    page.keyboard.press("Tab")  # modalidade de teclado → :focus-visible
    page.focus("#btn-play")
    focused, shadow = page.evaluate(
        "() => { const b = document.getElementById('btn-play');"
        " return [b.matches(':focus-visible'), getComputedStyle(b).boxShadow]; }"
    )
    assert focused, "o botão deveria estar em :focus-visible"
    ring = page.evaluate(
        "() => { const d = document.createElement('div'); d.style.boxShadow = 'var(--focus-ring)';"
        " document.body.append(d); const v = getComputedStyle(d).boxShadow; d.remove(); return v; }"
    )
    assert shadow != "none" and shadow == ring, (shadow, ring)
