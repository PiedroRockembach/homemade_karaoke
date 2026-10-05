"""E2E no navegador (Playwright + Chrome do sistema) contra o servidor real. Dono: qa-engineer (T-009).

Cobre: fluxo Controle→Palco com músicas semeadas, sincronia de comandos Controle↔Palco (≤ 300 ms),
regra de posse na UI, estados do Palco (início, espera, tocando, sem letra, pausado, A seguir, espelho),
atalhos do Palco, estados do Controle e ausência de requests externos.
"""

from __future__ import annotations

import time

import pytest

from .conftest import Live, collect_external_requests

pytestmark = pytest.mark.e2e

LRC = "[00:00.50]Primeira linha da letra\n[00:03.00]Segunda linha cantada\n[00:06.00]Terceira linha aqui\n"
SONG_1 = "AAAAAAAAAA1"
SONG_2 = "BBBBBBBBBB2"
SONG_3 = "CCCCCCCCCC3"


def scene(page) -> str | None:
    return page.evaluate("() => document.querySelector('.scene.on')?.dataset.scene || null")


def wait_scene(page, name: str, timeout: float = 10.0) -> None:
    page.wait_for_function(
        "n => document.querySelector('.scene.on')?.dataset.scene === n", arg=name, timeout=timeout * 1000
    )


def open_stage(context, live: Live, ext: list[str] | None = None):
    """Abre o Palco num *contexto separado* (janela própria): duas abas no mesmo contexto headless
    fazem a aba de trás ter rAF/estabilidade estrangulados e o `click` do Playwright atrasar ~1 s
    (artefato do harness, não do app)."""
    page = context.browser.new_context(viewport={"width": 1280, "height": 720}).new_page()
    if ext is not None:
        collect_external_requests(page, live.base, ext)
    page.goto(f"{live.base}/stage")
    return page


def open_control(context, live: Live, ext: list[str] | None = None):
    page = context.new_page()
    if ext is not None:
        collect_external_requests(page, live.base, ext)
    page.goto(f"{live.base}/")
    page.wait_for_selector("#tab-search")
    return page


# ---------------------------------------------------------------------------------------------
def test_stage_empty_queue_shows_waiting_screen(live: Live, context) -> None:
    """AC-06.5 / AC-06.6: Palco sem fila → 'Pressione Enter' e, após Enter, tela de espera."""
    page = open_stage(context, live)
    wait_scene(page, "start")
    assert "Pressione" in page.inner_text(".scene.on")
    page.keyboard.press("Enter")
    wait_scene(page, "idle")
    assert "fila está vazia" in page.inner_text(".scene.on")
    assert live.player()["stage"]["audio_owner"], "Enter deveria reivindicar o áudio (claim)"


def test_control_to_stage_flow_with_seeded_songs(live: Live, context) -> None:
    """Fluxo Controle→Palco: biblioteca → Fila → Palco toca com letra; auto-avanço; fim da fila."""
    live.seed_song(SONG_1, "Canção Um", seconds=8, lrc=LRC)
    live.seed_song(SONG_2, "Canção Dois", seconds=6)  # sem letra
    ext: list[str] = []
    control = open_control(context, live, ext)
    stage = open_stage(context, live, ext)
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    wait_scene(stage, "idle")

    # Controle: Biblioteca → Adicionar à fila (pede o cantor num modal)
    control.bring_to_front()
    control.click("#tab-lib")
    control.wait_for_selector(f".lib-row[data-id='{SONG_1}']")
    assert control.locator(".lib-row").count() == 2
    control.click("button[aria-label='Adicionar Canção Um à fila']")
    dialog = control.locator("[role=dialog]")
    if dialog.count():
        dialog.locator("input").first.fill("Ana")
        dialog.locator("button.btn-primary").first.click()

    # Palco começa a tocar sozinho (autoplay) com a letra
    wait_scene(stage, "play", timeout=15)
    stage.wait_for_function(
        "() => document.querySelector('[data-lyrics]')?.textContent.includes('linha')", timeout=8000
    )
    assert "Canção Um" in stage.inner_text("[data-song-title]")
    assert "Ana" in stage.inner_text("[data-song-singer]")
    # AC-06.1: linha atual >= 5 vh
    stage.wait_for_selector("[data-lyrics] .line.cur", timeout=5000)
    ratio = stage.evaluate(
        "() => parseFloat(getComputedStyle(document.querySelector('[data-lyrics] .line.cur')).fontSize) / innerHeight"
    )
    assert ratio >= 0.05, f"linha atual = {ratio * 100:.1f} vh"
    live.wait(lambda: live.player()["status"] == "playing", msg="player playing")
    # posição avança guiada pelo áudio
    p1 = live.player()["position_ms"]
    time.sleep(1.2)
    assert live.player()["position_ms"] > p1

    # adiciona a música sem letra → A seguir → toca → "Letra indisponível"
    control.click("button[aria-label='Adicionar Canção Dois à fila']")
    dialog = control.locator("[role=dialog]")
    if dialog.count():
        dialog.locator("button.btn-primary").first.click()
    live.wait(lambda: len(live.get("/api/queue")["items"]) == 1, msg="2º item na fila")
    wait_scene(stage, "upnext", timeout=15)
    assert "Canção Dois" in stage.inner_text(".scene.on")
    wait_scene(stage, "play", timeout=15)
    stage.wait_for_function(
        "() => document.getElementById('stage').classList.contains('nolyrics')", timeout=5000
    )
    # fim da fila → tela de espera
    wait_scene(stage, "idle", timeout=20)
    assert live.player()["status"] == "ended"
    assert ext == [], f"requests externos: {ext}"


def test_command_sync_control_stage_under_300ms(live: Live, context) -> None:
    """AC-07.2: play/pause no Controle reflete no Palco ≤ 300 ms e Espaço no Palco reflete no Controle."""
    live.seed_song(SONG_1, "Canção Um", seconds=30, lrc=LRC)
    control = open_control(context, live)
    stage = open_stage(context, live)
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    live.enqueue(SONG_1, "Bia")
    wait_scene(stage, "play", timeout=15)
    live.wait(lambda: live.player()["status"] == "playing")
    control.wait_for_function(
        "() => document.getElementById('btn-play').getAttribute('aria-label') === 'Pausar'"
    )

    # Controle → Palco
    samples = []
    for i in range(4):
        want_paused = i % 2 == 0
        stage.evaluate(
            """want => { window.__t = null; const root = document.getElementById('stage');
            const obs = new MutationObserver(() => { if (root.classList.contains('paused') === want && !window.__t)
              { window.__t = performance.timeOrigin + performance.now(); obs.disconnect(); } });
            obs.observe(root, {attributes: true}); }""",
            want_paused,
        )
        t0 = control.evaluate("() => performance.timeOrigin + performance.now()")
        control.click("#btn-play")
        stage.wait_for_function("() => window.__t !== null", timeout=3000)
        samples.append(stage.evaluate("() => window.__t") - t0)
        time.sleep(0.3)
    assert max(samples) <= 300, f"Controle→Palco ms: {samples}"

    # Palco → Controle (Espaço)
    back = []
    for _ in range(2):
        label_before = control.get_attribute("#btn-play", "aria-label")
        control.evaluate(
            """before => { window.__t = null; const b = document.getElementById('btn-play');
            const obs = new MutationObserver(() => { if (b.getAttribute('aria-label') !== before && !window.__t)
              { window.__t = performance.timeOrigin + performance.now(); obs.disconnect(); } });
            obs.observe(b, {attributes: true}); }""",
            label_before,
        )
        t0 = stage.evaluate("() => performance.timeOrigin + performance.now()")
        stage.keyboard.press("Space")
        control.wait_for_function("() => window.__t !== null", timeout=3000)
        back.append(control.evaluate("() => window.__t") - t0)
        time.sleep(0.3)
    assert max(back) <= 300, f"Palco→Controle ms: {back}"


def test_ownership_rule_in_ui(live: Live, context) -> None:
    """AC-04.4 / AC-05.5: UI esconde Excluir/Remover de itens de outro ator; mostra nos do host."""
    live.seed_song(SONG_1, "Do Host", seconds=8)
    live.seed_song(SONG_2, "Do Convidado", seconds=8, added_by="g_9")
    live.http.patch("/api/settings", json={"autoplay": False})
    live.enqueue(SONG_1, "Host")
    live.enqueue(SONG_1, "Host 2")
    live.seed_queue_item_as(SONG_2, "g_9", "Convidado")
    control = open_control(context, live)
    control.click("#tab-lib")
    control.wait_for_selector(f".lib-row[data-id='{SONG_2}']")
    assert control.locator("button[aria-label='Excluir Do Host']").count() == 1
    assert control.locator("button[aria-label='Excluir Do Convidado']").count() == 0
    q = control.locator("#queue")
    q.locator(".q-item").first.wait_for()
    assert q.locator("button[aria-label='Remover Do Convidado da fila']").count() == 0
    assert q.locator("button[aria-label='Remover Do Host da fila']").count() >= 1
    # servidor confirma 403 mesmo se a UI fosse burlada
    gid = [i for i in live.get("/api/queue")["items"] if i["added_by"] == "g_9"][0]["id"]
    r = live.http.delete(f"/api/queue/{gid}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
    r = live.http.delete(f"/api/songs/{SONG_2}")
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"


def test_stage_keyboard_and_states(live: Live, context) -> None:
    """AC-07.1/AC-07.4/AC-06.4: atalhos do Palco, overlay '?', pausado, offset, auto-avanço desligado."""
    live.seed_song(SONG_1, "Canção Um", seconds=30, lrc=LRC)
    live.seed_song(SONG_2, "Canção Dois", seconds=30, lrc=LRC)
    stage = open_stage(context, live)
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    live.enqueue(SONG_1, "Ana")
    live.enqueue(SONG_2, "Bruno")
    wait_scene(stage, "play", timeout=15)
    live.wait(lambda: live.player()["status"] == "playing")
    # "A seguir" no canto
    assert "Canção Dois" in stage.inner_text("[data-next]")

    stage.keyboard.press("Space")
    live.wait(lambda: live.player()["status"] == "paused", msg="pausado")
    stage.wait_for_function("() => document.getElementById('stage').classList.contains('paused')")
    stage.keyboard.press("Space")
    live.wait(lambda: live.player()["status"] == "playing")

    vol = live.player()["volume"]
    stage.keyboard.press("ArrowDown")
    live.wait(lambda: abs(live.player()["volume"] - (vol - 0.05)) < 1e-6, msg="volume -5%")
    stage.wait_for_function("() => document.querySelector('.hud')?.classList.contains('on')", timeout=2000)

    stage.keyboard.press("]")
    live.wait(lambda: live.player()["offset_ms"] == 50, msg="offset +50")
    assert live.get(f"/api/songs/{SONG_1}")["offset_ms"] == 50  # persistido na música
    stage.keyboard.press("[")
    stage.keyboard.press("[")
    live.wait(lambda: live.player()["offset_ms"] == -50, msg="offset -50")

    stage.keyboard.press("Shift+Equal")  # "+" voz guia
    live.wait(lambda: live.player()["guide_vocal"] > 0, msg="voz guia +")
    pos = live.player()["position_ms"]
    stage.keyboard.press("ArrowRight")
    live.wait(lambda: live.player()["position_ms"] >= pos + 4000, msg="seek +5 s")

    stage.keyboard.press("Shift+Slash")  # "?"
    stage.wait_for_selector("[data-help]:not([hidden])")
    stage.keyboard.press("Escape")
    stage.wait_for_selector("[data-help]", state="hidden")

    stage.keyboard.press("a")
    live.wait(lambda: live.player()["autoplay"] is False, msg="auto-avanço off")
    assert live.command("seek", 28_500).status_code == 200
    wait_scene(stage, "upnext", timeout=10)
    assert live.player()["up_next"]["starts_at"] is None  # manual: espera Enter
    stage.wait_for_selector("[data-up-hints-manual]:not([hidden])")
    stage.keyboard.press("Enter")
    wait_scene(stage, "play")
    assert "Canção Dois" in stage.inner_text("[data-song-title]")


def test_two_stages_mirror_and_claim(live: Live, browser) -> None:
    """DECISION 0006: dois Palcos reais; o 2º vira espelho e Enter nele transfere o áudio."""
    live.seed_song(SONG_1, "Canção Um", seconds=30, lrc=LRC)
    ctx1 = browser.new_context(viewport={"width": 1280, "height": 720})
    ctx2 = browser.new_context(viewport={"width": 1280, "height": 720})
    try:
        s1 = open_stage(ctx1, live)
        wait_scene(s1, "start")
        s1.keyboard.press("Enter")
        live.enqueue(SONG_1)
        wait_scene(s1, "play", timeout=15)
        owner1 = live.wait(lambda: live.player()["stage"]["audio_owner"])
        s2 = open_stage(ctx2, live)
        s2.wait_for_function(
            "() => document.getElementById('stage').classList.contains('mirror')", timeout=8000
        )
        assert live.player()["stage"]["connected"] == 2
        s2.keyboard.press("Enter")
        live.wait(lambda: live.player()["stage"]["audio_owner"] not in (None, owner1), msg="claim do 2º")
        s1.wait_for_function(
            "() => document.getElementById('stage').classList.contains('mirror')", timeout=5000
        )
        s2.wait_for_function(
            "() => !document.getElementById('stage').classList.contains('mirror')", timeout=5000
        )
    finally:
        ctx1.close()
        ctx2.close()


def test_control_states_and_shortcut(live: Live, context) -> None:
    """US-01 estados: vazio com '/', erro de rede na busca (servidor sem rede simulado não é possível aqui:
    usa query que falha?) — aqui: vazio + atalho '/', biblioteca vazia, fila vazia, tema alterna."""
    control = open_control(context, live)
    assert control.locator("#pane-search").inner_text().strip() != ""
    control.click("body")
    control.keyboard.press("/")
    assert control.evaluate("() => document.activeElement.id") == "q"
    control.click("#tab-lib")
    control.wait_for_function("() => document.getElementById('pane-lib').innerText.trim().length > 0")
    lib_text = control.inner_text("#pane-lib")
    assert "Buscar" in lib_text or "busca" in lib_text.lower()
    assert control.inner_text("#queue").strip() != ""
    before = control.evaluate("() => document.documentElement.dataset.theme || ''")
    control.click("#btn-theme")
    control.wait_for_function("b => (document.documentElement.dataset.theme || '') !== b", arg=before)
    # sem scroll horizontal em 360 px
    control.set_viewport_size({"width": 360, "height": 740})
    time.sleep(0.3)
    assert control.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")


def test_stage_prep_and_error_scenes(live: Live, context) -> None:
    """Estados do Palco: 'Aguardando preparo' (próxima ainda processando) e erro ao tocar (pula sozinho)."""
    live.seed_song(SONG_1, "Arquivo Quebrado", seconds=6, lrc=LRC)
    live.seed_song(SONG_2, "Canção Boa", seconds=20, lrc=LRC)
    # corrompe o áudio da 1ª
    (live.data_dir / "songs" / SONG_1 / "instrumental.opus").write_bytes(b"not an ogg file" * 100)
    stage = open_stage(context, live)
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    wait_scene(stage, "idle")
    live.enqueue(SONG_1)
    live.enqueue(SONG_2)
    wait_scene(stage, "error", timeout=15)
    assert "Não deu para tocar" in stage.inner_text(".scene.on")
    wait_scene(stage, "play", timeout=15)  # pulou sozinho após a contagem
    assert "Canção Boa" in stage.inner_text("[data-song-title]")
    hist = live.get("/api/queue")["history"]
    assert hist and hist[0]["status"] == "skipped"

    # 'Aguardando preparo': música em processamento no topo da fila, nada tocando
    live.command("next")
    wait_scene(stage, "idle", timeout=10)
    conn = live.db()
    try:
        conn.execute(
            "INSERT INTO songs(id,title,artist,duration_ms,status,added_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                SONG_3,
                "Ainda Preparando",
                "X",
                200000,
                "processing",
                "host",
                "2026-10-04T00:00:00Z",
                "2026-10-04T00:00:00Z",
            ),
        )
        conn.execute(
            "INSERT INTO jobs(id,kind,song_id,stage,progress,stage_progress,created_at,updated_at) "
            "VALUES ('j_900','process',?,'separating',0.5,0.4,'2026-10-04T00:00:00Z','2026-10-04T00:00:00Z')",
            (SONG_3,),
        )
    finally:
        conn.close()
    live.enqueue(SONG_3)
    wait_scene(stage, "prep", timeout=10)
    assert "Ainda Preparando" in stage.inner_text(".scene.on")


def test_control_transport_next_prev_seek(live: Live, context) -> None:
    """AC-07.1 pelo Controle: próxima, anterior/reiniciar e seek refletem no Palco."""
    live.seed_song(SONG_1, "Canção Um", seconds=30, lrc=LRC)
    live.seed_song(SONG_2, "Canção Dois", seconds=30, lrc=LRC)
    control = open_control(context, live)
    stage = open_stage(context, live)
    wait_scene(stage, "start")
    stage.keyboard.press("Enter")
    live.enqueue(SONG_1)
    live.enqueue(SONG_2)
    wait_scene(stage, "play", timeout=15)
    live.wait(lambda: live.player()["status"] == "playing")
    control.wait_for_function("() => !document.getElementById('seek').disabled")
    # seek pelo slider (envia ao soltar)
    control.evaluate(
        "() => { const s = document.getElementById('seek'); s.value = '20000';"
        " s.dispatchEvent(new Event('input', {bubbles: true})); s.dispatchEvent(new Event('change', {bubbles: true})); }"
    )
    live.wait(lambda: live.player()["position_ms"] >= 19_000, msg="seek do Controle")
    control.click("#btn-next")
    stage.wait_for_function(
        "() => document.querySelector('[data-song-title]')?.textContent.includes('Dois')", timeout=10000
    )
    control.click("#btn-prev")  # posição < 3 s → volta à anterior
    stage.wait_for_function(
        "() => document.querySelector('[data-song-title]')?.textContent.includes('Um')", timeout=10000
    )
