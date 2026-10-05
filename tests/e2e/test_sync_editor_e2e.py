"""E2E do editor de sincronia (US-09, DESIGN §11.6) e do menu Letra (US-08). Dono: qa-engineer (T-007, ciclo
20261004-1742).

Sem GPU/rede: músicas semeadas com áudio senoidal (conftest). Ficam de fora, de propósito:
- "Tocar e marcar": depende do relógio do `<audio>` tocando de verdade (tempo marcado = posição − reação);
  a lógica de marcação/âncora/desfazer é coberta por `test_sync_model_test.js` (markLine/record/restore).
- "Detectar automaticamente": precisa da transcrição da voz (Whisper) — coberto pela API com mocks em
  `tests/api/test_lyrics_v02.py` (detect-offset); o aplicar/desfazer do offset, por `test_sync_model_test.js`.
- Arrastar marcadores na onda: descartado pelo usuário.
"""

from __future__ import annotations

import pytest

from .conftest import Live

pytestmark = pytest.mark.e2e

SONG = "EEEEEEEEEE5"
LRC = "[00:00.50]Primeira linha\n[00:03.00]Segunda linha\n[00:06.00]Terceira linha\n"
HAS_SONG = ("waiting_gesture", "loading", "playing", "paused")


def _as_online(live: Live, song_id: str) -> None:
    """Faz a letra semeada (colada → Manual) passar por "Online" (lrclib), para provar a troca de origem."""
    conn = live.db()
    try:
        conn.execute(
            "UPDATE lyrics_candidates SET source='lrclib', lyrics_json=json_replace(lyrics_json,'$.source','lrclib')"
            " WHERE song_id=?",
            (song_id,),
        )
    finally:
        conn.close()


def _lyrics(live: Live) -> dict:
    return live.get(f"/api/songs/{SONG}/lyrics")


def test_sync_editor_adjust_undo_leave_dialog_and_save(live: Live, context) -> None:
    """Abrir com E → ajustar linha (ponto de alterada) → Ctrl+Z → ajustar de novo → Esc pede "Sair sem salvar?" →
    Continuar → Ctrl+S salva (PUT manual-timing) → origem "Manual" na API → Esc fecha sem perguntar."""
    live.seed_song(SONG, "Canção Editor", seconds=12, lrc=LRC)
    _as_online(live, SONG)
    assert _lyrics(live)["source"] == "lrclib"
    live.enqueue(SONG, "Ana")
    live.wait(lambda: live.player()["status"] in HAS_SONG, msg="música carregada")

    page = context.new_page()
    page.goto(f"{live.base}/")
    page.wait_for_selector("#tab-search")
    page.wait_for_function("() => !document.getElementById('btn-play').disabled", timeout=8000)
    page.locator("body").click(position={"x": 5, "y": 5})  # foco fora de campos de texto
    page.keyboard.press("e")

    ed = page.locator(".ed[role=dialog]")
    ed.wait_for()
    page.wait_for_selector("#srow-2")
    changed = page.locator(".srow .dot:not(.off)")
    assert changed.count() == 0
    t0 = page.inner_text("#srow-0 .tv")

    # ajuste fino +100 ms na linha 1 (Mover as seguintes ligado por padrão → as 3 linhas andam)
    page.wait_for_function("() => document.activeElement && document.activeElement.dataset.fk === 'list'")
    page.keyboard.press("Shift+ArrowRight")
    page.wait_for_function("() => document.querySelectorAll('.srow .dot:not(.off)').length === 3")
    assert page.inner_text("#srow-0 .tv") != t0

    # desfazer
    page.keyboard.press("Control+z")
    page.wait_for_function("() => document.querySelectorAll('.srow .dot:not(.off)').length === 0")
    assert page.inner_text("#srow-0 .tv") == t0

    # altera de novo e tenta sair: diálogo interno "Sair sem salvar?"; Esc volta a editar
    page.keyboard.press("Shift+ArrowRight")
    page.wait_for_function("() => document.querySelectorAll('.srow .dot:not(.off)').length === 3")
    page.keyboard.press("Escape")
    leave = page.get_by_role("alertdialog", name="Sair sem salvar?")
    leave.wait_for()
    assert "3 alterações" in leave.inner_text()
    page.keyboard.press("Escape")
    leave.wait_for(state="detached")
    assert ed.is_visible()
    assert _lyrics(live)["source"] == "lrclib", "nada salvo antes do Ctrl+S"

    # salvar
    page.keyboard.press("Control+s")
    page.get_by_text("Sincronia salva").first.wait_for()
    doc = live.wait(lambda: (lambda d: d if d["source"] == "manual" else None)(_lyrics(live)), msg="manual")
    assert [ln["start_ms"] for ln in doc["lines"]] == [600, 3100, 6100]
    song = live.get(f"/api/songs/{SONG}")
    assert song["lyrics"]["source"] == "manual"
    page.wait_for_function("() => document.querySelectorAll('.srow .dot:not(.off)').length === 0")

    # sem alterações pendentes, Esc fecha direto
    page.keyboard.press("Escape")
    ed.wait_for(state="detached")


def test_library_lyrics_menu_items_and_editor_from_menu(live: Live, context) -> None:
    """Menu Letra ▾ da Biblioteca lista as 4 ações; "Editar sincronia…" abre o editor; sair sem mudanças fecha."""
    live.seed_song(SONG, "Canção Editor", seconds=12, lrc=LRC)
    page = context.new_page()
    page.goto(f"{live.base}/")
    page.click("#tab-lib")
    page.wait_for_selector(f".lib-row[data-id='{SONG}']")
    page.click("button[aria-label='Gerir letra de Canção Editor']")
    menu = page.get_by_role("menu")
    menu.wait_for()
    names = [x.strip() for x in menu.get_by_role("menuitem").all_inner_texts()]
    for want in (
        "Editar sincronia…",
        "Buscar letra oficial e sincronizar",
        "Colar letra…",
        "Candidatas e colar LRC…",
    ):
        assert any(want in n for n in names), (want, names)
    menu.get_by_role("menuitem", name="Editar sincronia…").click()
    ed = page.locator(".ed[role=dialog]")
    ed.wait_for()
    page.wait_for_selector("#srow-2")
    assert "Primeira linha" in page.inner_text("#srow-0")
    page.wait_for_function("() => document.activeElement && document.activeElement.dataset.fk === 'list'")
    page.keyboard.press("Escape")
    ed.wait_for(state="detached")
