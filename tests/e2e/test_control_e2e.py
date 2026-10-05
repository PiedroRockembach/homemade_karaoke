"""E2E do Controle: fila (botões, teclado, arrastar, remover/desfazer, limpar), biblioteca (excluir com
confirmação), letra (colar LRC, offset) e mini-player (voz guia/volume persistidos). Dono: qa-engineer (T-009)."""

from __future__ import annotations

import time

import pytest

from .conftest import Live, start_server, stop_server

pytestmark = pytest.mark.e2e

LRC = "[00:00.50]Primeira linha\n[00:03.00]Segunda linha\n"
IDS = ["AAAAAAAAAA1", "BBBBBBBBBB2", "CCCCCCCCCC3"]
TITLES = ["Canção Um", "Canção Dois", "Canção Três"]


def pending_ids(live: Live) -> list[str]:
    return [i["song_id"] for i in live.get("/api/queue")["items"]]


def open_control(context, live: Live):
    page = context.new_page()
    page.goto(f"{live.base}/")
    page.wait_for_selector("#tab-search")
    return page


def seed_three(live: Live) -> None:
    for sid, t, f in zip(IDS, TITLES, (330, 440, 550), strict=True):
        live.seed_song(sid, t, seconds=8, freq=f, lrc=LRC)
    live.http.patch("/api/settings", json={"autoplay": False})


def test_queue_reorder_buttons_keyboard_drag_remove_clear(live: Live, context) -> None:
    """AC-05.2: reordenar por botões, Alt+↑/↓, arrastar; remover com Desfazer; limpar com confirmação."""
    seed_three(live)
    for sid in IDS:
        live.enqueue(sid)
    # sem Palco e autoplay off: tudo pendente
    assert pending_ids(live) == IDS
    page = open_control(context, live)
    q = page.locator("#queue")
    q.locator(".q-item").nth(2).wait_for()

    # botão Descer no 1º
    q.get_by_role("button", name="Descer Canção Um").click()
    live.wait(lambda: pending_ids(live) == [IDS[1], IDS[0], IDS[2]], msg="descer por botão")

    # teclado: foca o item 'Canção Três' e Alt+↑ duas vezes
    item3 = q.locator(".q-item", has_text="Canção Três")
    item3.focus()
    page.keyboard.press("Alt+ArrowUp")
    live.wait(lambda: pending_ids(live)[1] == IDS[2], msg="Alt+↑ 1")
    page.keyboard.press("Alt+ArrowUp")
    live.wait(lambda: pending_ids(live)[0] == IDS[2], msg="Alt+↑ 2")
    assert "Canção Três" in page.evaluate("() => document.activeElement.closest('.q-item')?.innerText || ''")

    # arrastar o último para o topo — antes, espera o DOM refletir a ordem do servidor (o SSE pode chegar
    # depois do GET; arrastar um item que está sendo re-renderizado é flaky)
    order = [TITLES[IDS.index(s)] for s in pending_ids(live)]
    live.wait(lambda: q.locator(".q-item .q-t").all_inner_texts() == order, msg="DOM na ordem do servidor")
    last = q.locator(".q-item").nth(2)
    first = q.locator(".q-item").nth(0)
    last_title = last.locator(".q-t").inner_text()
    last.drag_to(first, target_position={"x": 20, "y": 2})
    live.wait(lambda: live.get(f"/api/songs/{pending_ids(live)[0]}")["title"] == last_title, msg="drag")

    # remover com Desfazer
    before = pending_ids(live)
    victim = live.get(f"/api/songs/{before[1]}")["title"]
    q.get_by_role("button", name=f"Remover {victim} da fila").click()
    live.wait(lambda: len(pending_ids(live)) == 2, msg="remover")
    page.locator("#toasts").get_by_role("button", name="Desfazer").click()
    live.wait(lambda: pending_ids(live) == before, msg="desfazer na mesma posição")

    # Del no item focado
    q.locator(".q-item").nth(0).focus()
    page.keyboard.press("Delete")
    live.wait(lambda: len(pending_ids(live)) == 2, msg="Del remove")

    # limpar com confirmação
    page.click("#clear-queue")
    dlg = page.locator("[role=dialog]")
    dlg.wait_for()
    dlg.locator(".btn-danger, .btn-primary").last.click()
    live.wait(lambda: pending_ids(live) == [], msg="limpar")


def test_library_delete_with_confirmation_removes_files(live: Live, context) -> None:
    """AC-04.2: excluir pede confirmação (Cancelar não apaga); confirmar remove arquivos do disco."""
    seed_three(live)
    page = open_control(context, live)
    page.click("#tab-lib")
    page.wait_for_selector(f".lib-row[data-id='{IDS[0]}']")
    page.click("button[aria-label='Excluir Canção Um']")
    dlg = page.locator("[role=dialog]")
    dlg.wait_for()
    assert "Excluir" in dlg.inner_text()
    assert page.evaluate("() => document.activeElement.textContent.trim()") == "Cancelar"
    page.keyboard.press("Enter")  # foco em Cancelar → não apaga
    dlg.wait_for(state="detached")
    assert (live.data_dir / "songs" / IDS[0]).exists()
    page.click("button[aria-label='Excluir Canção Um']")
    dlg.wait_for()
    dlg.get_by_role("button", name="Excluir música").click()
    page.wait_for_selector(f".lib-row[data-id='{IDS[0]}']", state="detached")
    live.wait(lambda: not (live.data_dir / "songs" / IDS[0]).exists(), msg="arquivos apagados")
    assert live.http.get(f"/api/songs/{IDS[0]}").status_code == 404


def test_library_filter_and_lyrics_paste_and_offset(live: Live, context) -> None:
    """AC-04.1 (busca local sem acento), AC-03.4 (colar LRC pela UI, menu Letra ▾ da v0.2), AC-03.5 (offset salvo),
    AC-03.3 (origem). Atualizado no T-007 (ciclo 20261004-1742)."""
    seed_three(live)
    live.seed_song("DDDDDDDDDD4", "Sem Letra", seconds=8)
    page = open_control(context, live)
    page.click("#tab-lib")
    page.wait_for_selector(".lib-row[data-id='DDDDDDDDDD4']")
    page.fill("#libq", "cancao tres")  # sem acento/caixa
    time.sleep(0.3)
    assert page.locator(".lib-row:visible").count() == 1
    page.fill("#libq", "")
    row = page.locator(".lib-row[data-id='DDDDDDDDDD4']")
    assert "Manual" not in row.inner_text()
    # v0.2: o botão abre o menu Letra ▾; o modal de candidatas fica em "Candidatas e colar LRC…"
    page.click("button[aria-label='Gerir letra de Sem Letra']")
    page.get_by_role("menuitem", name="Candidatas e colar LRC…").click()
    dlg = page.locator("[role=dialog]")
    dlg.wait_for()
    dlg.get_by_role("tab", name="Colar letra").click()
    save_btn = dlg.get_by_role("button", name="Salvar letra colada")
    assert save_btn.is_disabled()  # campo vazio
    page.fill("#lrc", "isso não é LRC")  # texto puro é aceito (CR-001): os tempos viriam da voz
    assert save_btn.is_enabled()
    assert "Texto sem tempos" in page.inner_text("#lrc-hint")
    # não envia o texto puro (dispararia o alinhamento pela voz/Whisper); segue com um LRC com tempos
    page.fill("#lrc", "[00:01.00]Linha colada um\n[00:04.00]Linha colada dois")
    dlg.get_by_role("button", name="Salvar letra colada").click()
    live.wait(
        lambda: (live.get("/api/songs/DDDDDDDDDD4")["lyrics"] or {}).get("source") == "manual", msg="manual"
    )
    # offset +50 duas vezes no modal → persistido
    dlg.get_by_role("button", name="Letra 50 ms mais tarde").click()
    dlg.get_by_role("button", name="Letra 50 ms mais tarde").click()
    live.wait(lambda: live.get("/api/songs/DDDDDDDDDD4")["offset_ms"] == 100, msg="offset 100")
    page.keyboard.press("Escape")
    dlg.wait_for(state="detached")
    live.wait(lambda: "Manual" in row.inner_text() or "manual" in row.inner_text().lower(), msg="chip origem")


def test_player_settings_persist_across_restart(live: Live, context, tmp_path) -> None:
    """AC-07.3 (voz guia padrão 0% e persistida), volume persistido; AC-05.4/AC-04.3 (fila e biblioteca após
    reiniciar)."""
    seed_three(live)
    assert live.player()["guide_vocal"] == 0
    live.enqueue(IDS[0], "Ana")
    live.enqueue(IDS[1], "Bia")
    page = open_control(context, live)
    page.locator("#guide").fill("40")
    page.locator("#guide").dispatch_event("change")
    page.locator("#vol").fill("55")
    page.locator("#vol").dispatch_event("change")
    live.wait(lambda: abs(live.player()["guide_vocal"] - 0.4) < 0.01, msg="voz guia 40%")
    live.wait(lambda: abs(live.player()["volume"] - 0.55) < 0.01, msg="volume 55%")
    page.close()
    data_dir = live.data_dir
    queue_before = live.get("/api/queue")
    stop_server(live)
    again = start_server(data_dir)
    try:
        p = again.player()
        assert abs(p["guide_vocal"] - 0.4) < 0.01 and abs(p["volume"] - 0.55) < 0.01
        q = again.get("/api/queue")
        assert [i["id"] for i in q["items"]] == [i["id"] for i in queue_before["items"]]
        assert [i["singer"] for i in q["items"]] == ["Ana", "Bia"]
        assert again.get("/api/songs")["total"] == 3
    finally:
        stop_server(again)


def test_control_sliders_reflect_server_state_on_load(live: Live, context) -> None:
    """AC-07.3: ao abrir/recarregar o Controle, Volume e Voz guia mostram o valor persistido no servidor."""
    seed_three(live)
    assert live.command("guide_vocal", 0.4).status_code == 200
    assert live.command("volume", 0.55).status_code == 200
    page = open_control(context, live)
    time.sleep(2)
    got = page.evaluate(
        "() => [document.getElementById('guide-out').textContent.trim(),"
        " document.getElementById('vol-out').textContent.trim()]"
    )
    assert got == ["40%", "55%"], got
