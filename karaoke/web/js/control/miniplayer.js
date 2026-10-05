/**
 * Mini-player (US-07): agora tocando, anterior/play-pause/próxima, auto-avanço, seek, volume, voz guia,
 * offset da letra (ajuste rápido), atalho para a letra e "Editar sincronia" (v0.2, tecla E). Reflete `player.state`/`player.position` em tempo real; comandos
 * vão para `POST /api/player/command` (o estado volta pelo SSE — o servidor é a fonte de verdade).
 *
 * Dono: frontend-engineer (T-008).
 */

import { h, icon, replace } from "../shared/components/dom.js";
import { thumb } from "../shared/components/song-card.js";
import { fmtOffset, fmtTime } from "../shared/format.js";
import { extrapolatePosition } from "../shared/state.js";
import { command } from "./actions.js";
import { openLyrics } from "./lyrics-panel.js";
import { openSyncEditor } from "./sync-editor.js";

const HAS_SONG = ["waiting_gesture", "loading", "playing", "paused"];
const USER_HOLD_MS = 700; // após mexer num slider, ignora o estado do servidor por um instante
const SEND_EVERY_MS = 120;

let store = null;
let els = {};
// -Infinity: ninguém mexeu ainda. Com 0, o `hello` que chega < 700 ms após a navegação era descartado (QA-1 M-02).
const hold = { seek: -Infinity, vol: -Infinity, guide: -Infinity };
let seeking = false;

export function initMiniplayer(opts) {
  store = opts.store;
  const $ = (id) => document.getElementById(id);
  els = {
    root: $("player"),
    np: $("np"),
    prev: $("btn-prev"),
    play: $("btn-play"),
    next: $("btn-next"),
    auto: $("btn-auto"),
    seek: $("seek"),
    pos: $("pos"),
    dur: $("dur"),
    vol: $("vol"),
    volOut: $("vol-out"),
    guide: $("guide"),
    guideOut: $("guide-out"),
    offMinus: $("off-minus"),
    offPlus: $("off-plus"),
    offOut: $("off-out"),
    lyrics: $("btn-lyrics"),
    syncEdit: $("btn-sync-edit"),
  };
  els.prev.addEventListener("click", () => command("prev"));
  els.next.addEventListener("click", () => command("next"));
  els.play.addEventListener("click", onPlay);
  els.auto.addEventListener("click", () => {
    const p = store.get().player;
    command("autoplay", !(p && p.autoplay));
  });
  els.offMinus.addEventListener("click", () => command("offset_relative", -50));
  els.offPlus.addEventListener("click", () => command("offset_relative", 50));
  els.lyrics.addEventListener("click", () => {
    const p = store.get().player;
    if (p && p.song_id) openLyrics(p.song_id);
  });
  els.syncEdit.addEventListener("click", editCurrent);

  // Seek: arrastar só atualiza o rótulo; soltar (change) envia.
  els.seek.addEventListener("input", () => {
    seeking = true;
    hold.seek = performance.now();
    fill(els.seek);
    els.pos.textContent = fmtTime(Number(els.seek.value));
  });
  els.seek.addEventListener("change", () => {
    seeking = false;
    hold.seek = performance.now();
    command("seek", Math.round(Number(els.seek.value)));
  });

  wireLevel(els.vol, els.volOut, "vol", "volume");
  wireLevel(els.guide, els.guideOut, "guide", "guide_vocal");

  store.subscribe((_s, keys) => {
    if (keys.includes("player") || keys.includes("queue")) render();
  });
  render();
  setInterval(updatePosition, 250);
}

/** Abre o editor de sincronia da música atual (botão e tecla E). Retorna false se não há música. */
export function editCurrent() {
  const p = store && store.get().player;
  if (!p || !p.song_id || !HAS_SONG.includes(p.status)) return false;
  openSyncEditor(p.song_id, { fromPlayer: true });
  return true;
}

/** Volume / voz guia: envia com limite de taxa durante o arraste e o valor final no change. */
function wireLevel(input, out, holdKey, action) {
  let last = 0;
  let timer = 0;
  const send = () => {
    last = performance.now();
    command(action, Number(input.value) / 100);
  };
  input.addEventListener("input", () => {
    hold[holdKey] = performance.now();
    fill(input);
    out.textContent = `${input.value}%`;
    clearTimeout(timer);
    const wait = SEND_EVERY_MS - (performance.now() - last);
    if (wait <= 0) send();
    else timer = setTimeout(send, wait);
  });
  input.addEventListener("change", () => {
    hold[holdKey] = performance.now();
    clearTimeout(timer);
    send();
  });
}

/** Preenchimento da trilha do range (CSSOM; sem style inline). */
function fill(input) {
  const max = Number(input.max) || 1;
  input.style.setProperty("--val", `${(Number(input.value) / max) * 100}%`);
}

function onPlay() {
  const p = store.get().player;
  if (!p) return;
  if (p.status === "up_next" || p.status === "idle" || p.status === "ended") command("play");
  else command("toggle");
}

function held(key) {
  return performance.now() - hold[key] < USER_HOLD_MS;
}

function hasReadyPending(q) {
  return Boolean(q && (q.items || []).some((it) => it.ready));
}

function nowPlaying(p) {
  if (p && HAS_SONG.includes(p.status) && p.song) {
    const s = p.song;
    let sub;
    if (p.status === "waiting_gesture") {
      sub = h(
        "span",
        { class: "np-warn", title: "Aguardando o Palco — pressione Enter na TV para liberar o som" },
        "Aguardando o Palco (Enter na TV)",
      );
    } else if (p.status === "loading") sub = "Carregando…";
    else {
      sub = [s.artist || "", p.singer ? [s.artist ? " · " : "", "cantando: ", h("b", {}, p.singer)] : ""];
    }
    return [
      thumb({ src: s.thumbnail }),
      h(
        "div",
        { class: "np-text" },
        h("div", { class: "t", title: s.title }, s.title),
        h("div", { class: "s" }, sub),
      ),
    ];
  }
  if (p && p.status === "up_next" && p.up_next && p.up_next.song) {
    const s = p.up_next.song;
    return [
      thumb({ src: s.thumbnail }),
      h(
        "div",
        { class: "np-text" },
        h("div", { class: "np-label" }, "A seguir"),
        h("div", { class: "t", title: s.title }, s.title),
        h(
          "div",
          { class: "s" },
          p.up_next.singer ? ["cantando: ", h("b", {}, p.up_next.singer)] : s.artist || "",
        ),
      ),
    ];
  }
  return [
    h("div", { class: "thumb" }, h("div", { class: "ph" }, icon("music"))),
    h(
      "div",
      { class: "np-text" },
      h("div", { class: "t" }, "Nada tocando"),
      h("div", { class: "s" }, "Adicione músicas à fila para começar"),
    ),
  ];
}

function render() {
  const s = store.get();
  const p = s.player;
  const q = s.queue;
  const status = p ? p.status : "idle";
  const has = Boolean(p && HAS_SONG.includes(status) && p.song);
  const canStart = has || status === "up_next" || hasReadyPending(q);

  const npKey = JSON.stringify([
    status,
    p && p.song_id,
    p && p.singer,
    p && p.song && p.song.title,
    p && p.song && p.song.thumbnail,
    p && p.up_next && p.up_next.queue_item_id,
  ]);
  if (els.np.dataset.key !== npKey) {
    replace(els.np, nowPlaying(p));
    els.np.dataset.key = npKey;
  }

  // Transporte
  const playing = status === "playing";
  const loading = status === "loading";
  els.play.disabled = !canStart;
  els.play.classList.toggle("is-loading", loading);
  const playIcon = loading ? "loader" : playing ? "pause" : "play";
  if (els.play.dataset.icon !== playIcon) {
    replace(els.play, icon(playIcon, "icon-lg"));
    els.play.dataset.icon = playIcon;
  }
  const playLabel = loading
    ? "Carregando…"
    : playing
    ? "Pausar"
    : status === "up_next"
    ? "Começar a próxima agora"
    : "Tocar";
  els.play.setAttribute("aria-label", playLabel);
  els.play.title = `${playLabel} (Espaço no Palco)`;
  els.prev.disabled = !has && !(q && q.history && q.history.length);
  els.next.disabled = !has && status !== "up_next" && !hasReadyPending(q);
  const auto = Boolean(p && p.autoplay);
  els.auto.setAttribute("aria-pressed", String(auto));
  els.auto.title = auto
    ? "Auto-avanço ligado: ao terminar, toca a próxima da fila"
    : "Auto-avanço desligado: a próxima espera você";

  // Seek
  const dur = (p && (p.duration_ms || (p.song && p.song.duration_ms))) || 0;
  const canSeek = has && dur > 0 && (status === "playing" || status === "paused");
  els.seek.disabled = !canSeek;
  els.seek.max = String(Math.max(1, dur));
  els.dur.textContent = fmtTime(has ? dur : 0);
  updatePosition();

  // Mixer
  if (p && !held("vol")) {
    const v = Math.round((p.volume ?? 0) * 100);
    els.vol.value = String(v);
    els.volOut.textContent = `${v}%`;
    fill(els.vol);
  }
  if (p && !held("guide")) {
    const g = Math.round((p.guide_vocal ?? 0) * 100);
    els.guide.value = String(g);
    els.guideOut.textContent = `${g}%`;
    fill(els.guide);
  }
  const canOffset = Boolean(p && p.song_id && has);
  els.offMinus.disabled = !canOffset;
  els.offPlus.disabled = !canOffset;
  els.lyrics.disabled = !canOffset;
  els.syncEdit.disabled = !canOffset;
  els.offOut.textContent = fmtOffset(canOffset ? (p.offset_ms ?? (p.song && p.song.offset_ms) ?? 0) : 0);
}

function updatePosition() {
  const s = store.get();
  const p = s.player;
  if (seeking || held("seek")) return;
  const has = Boolean(p && HAS_SONG.includes(p.status) && p.song);
  const pos = has ? extrapolatePosition(p, s.serverSkewMs) : 0;
  const v = String(Math.round(pos));
  if (els.seek.value !== v) {
    els.seek.value = v;
    fill(els.seek);
  }
  const label = fmtTime(pos);
  if (els.pos.textContent !== label) els.pos.textContent = label;
}
