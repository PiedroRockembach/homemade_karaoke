/**
 * Editor de sincronia (US-09, DESIGN §11.6): modal em tela cheia no Controle com player próprio (`<audio>` do
 * instrumental + voz-guia), forma de onda da voz, lista de linhas com destaque ao vivo, modos "Ajustar" e
 * "Tocar e marcar", ajuste fino ±10/±100 ms, offset geral com prévia e "Detectar automaticamente",
 * desfazer/refazer, Descartar/Salvar (`PUT …/manual-timing` e/ou `PATCH /api/songs/{id}`) e confirmação ao sair.
 *
 * Contrato: api.md §7 (`/lyrics`, `/lyrics/candidates`, `/manual-timing`, `/detect-offset`, `/align`) e §2 (mídia).
 * Dono: frontend-engineer (T-003).
 */

import { api } from "../shared/api.js";
import { emptyState, h, icon, replace } from "../shared/components/dom.js";
import { lyricsSourceChip } from "../shared/components/song-card.js";
import { fmtClock, fmtOffset, fmtTime } from "../shared/format.js";
import { extrapolatePosition } from "../shared/state.js";
import { command } from "./actions.js";
import {
  activeLyricsJob,
  alignLyrics,
  analyzePasted,
  lyricsJobLabel,
  pasteHint,
  submitPastedLyrics,
} from "./lyrics-actions.js";
import { openLyrics } from "./lyrics-panel.js";
import * as M from "./sync-model.js";
import { toast } from "./ui.js";
import { drawOverview, drawWindow, loadPeaks } from "./waveform.js";

const LS_RATE = "karaoke.editor.rate";
const LS_REACT = "karaoke.editor.react";
const OFFSET_LIMIT = 5000;
const WINDOW_MS = 10000;
const WAVE_TIMEOUT_MS = 4000;
const SCROLL_PAUSE_MS = 4000;
const DRIFT_HIGH = 100; // ms/min: acima disso um offset fixo não resolve
const RATES = [0.5, 0.75, 1];

let store = null;
/** Estado do editor aberto (um por vez). */
let ed = null;

export function initSyncEditor(opts) {
  store = opts.store;
}

/** true se o editor está aberto (atalhos globais do Controle ficam desligados). */
export function editorOpen() {
  return ed !== null;
}

function lsGet(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v == null ? fallback : Number(v);
  } catch {
    return fallback;
  }
}

function lsSet(key, value) {
  try {
    localStorage.setItem(key, String(value));
  } catch {
    /* sem localStorage: vale só nesta sessão */
  }
}

const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// ======================================================================== abrir / fechar

/**
 * Abre o editor da música.
 * @param {string} songId
 * @param {{fromPlayer?: boolean}} [opts] `fromPlayer`: aberto pelo "tocando agora" (pausa o Palco e começa na
 *   posição atual)
 */
export function openSyncEditor(songId, { fromPlayer = false } = {}) {
  if (ed) {
    if (ed.songId === songId) return;
    toast("Feche o editor aberto antes de editar outra música.", { level: "warning" });
    return;
  }
  const modalRoot = document.getElementById("modal-root");
  // fecha um modal comum aberto (ex.: "Letra"), sem devolver o foco
  const openBg = modalRoot && modalRoot.querySelector(".modal-bg .modal-head button[aria-label='Fechar']");
  if (openBg) openBg.click();

  const p = store && store.get().player;
  const playing = Boolean(p && p.song_id === songId && p.status === "playing");
  const startAt = p && p.song_id === songId ? extrapolatePosition(p, store.get().serverSkewMs) : 0;

  ed = {
    songId,
    lastFocus: document.activeElement,
    song: null,
    cand: null,
    candId: null,
    lines: [],
    marked: [],
    offset: 0,
    savedOffset: 0,
    sel: 0,
    cursor: 0,
    mode: "adjust",
    rate: RATES.includes(lsGet(LS_RATE, 1)) ? lsGet(LS_RATE, 1) : 1,
    voice: true,
    ripple: true,
    react: Math.max(0, Math.min(300, lsGet(LS_REACT, 100))),
    hist: M.createHistory(),
    state: "loading",
    loadError: null,
    saving: false,
    banner: null,
    job: activeLyricsJob(songId),
    detect: null,
    audio: null,
    audioState: "loading",
    peaks: null,
    waveState: "loading",
    playIdx: -2,
    userScrollAt: 0,
    legendFull: false,
    pasteOpen: false,
    countdown: 0,
    startAt: fromPlayer ? startAt : 0,
    raf: 0,
    abort: new AbortController(),
    els: {},
    flash: -1,
  };
  build();
  if (fromPlayer && playing) {
    command("pause");
    toast("Palco pausado enquanto você edita", { level: "info" });
  }
  load();
}

function build() {
  const e = ed.els;
  e.head = h("div", { class: "ed-head" });
  e.banner = h("div", { class: "ed-banner-slot" });
  e.tools = h("div", { class: "ed-tools" });
  e.wave = h("div", { class: "ed-wave" });
  e.listH = h("div", { class: "ed-list-h" });
  e.list = h("div", {
    class: "ed-list",
    role: "listbox",
    tabindex: "0",
    "aria-label": "Linhas da letra. Setas ↑/↓ escolhem, ←/→ ajustam, Enter ouve",
    "data-fk": "list",
  });
  e.side = h("aside", { class: "ed-side", "aria-label": "Prévia e ajustes" });
  e.legend = h("div", { class: "ed-legend" });
  e.live = h("div", { class: "sr-only", "aria-live": "polite" });
  e.inner = h("div", { class: "ed-inner-slot" });
  e.box = h(
    "div",
    { class: "ed", role: "dialog", "aria-modal": "true", "aria-labelledby": "ed-title" },
    e.head,
    e.banner,
    e.tools,
    e.wave,
    h(
      "div",
      { class: "ed-body" },
      h("div", { class: "ed-list-wrap" }, e.listH, e.list),
      e.side,
    ),
    e.legend,
    e.live,
    e.inner,
  );
  e.bg = h("div", { class: "ed-bg" }, e.box);
  e.box.addEventListener("keydown", onKey);
  e.list.addEventListener("wheel", () => (ed.userScrollAt = performance.now()), { passive: true });
  e.list.addEventListener("touchmove", () => (ed.userScrollAt = performance.now()), { passive: true });
  e.list.addEventListener("click", onListClick);
  document.body.append(e.bg);
  document.body.classList.add("has-modal");
  addEventListener("beforeunload", onBeforeUnload);
  renderAll();
  focusFk("close");
  ed.raf = requestAnimationFrame(loop);
}

async function load() {
  const cur = ed;
  const id = encodeURIComponent(cur.songId);
  cur.state = "loading";
  cur.loadError = null;
  renderAll();
  try {
    const [song, doc, cands] = await Promise.all([
      api.get(`/api/songs/${id}`),
      api.get(`/api/songs/${id}/lyrics`),
      api.get(`/api/songs/${id}/lyrics/candidates`).catch(() => null),
    ]);
    if (ed !== cur) return;
    cur.song = song;
    cur.candId = doc.candidate_id || null;
    cur.cand = (cands && (cands.candidates || []).find((c) => c.id === cur.candId)) ||
      (doc.source ? { id: cur.candId, source: doc.source, label: null } : null);
    cur.lines = M.linesFromDoc(doc);
    cur.marked = cur.lines.map(() => false);
    cur.offset = song.offset_ms || 0;
    cur.savedOffset = cur.offset;
    cur.hist = M.createHistory();
    cur.sel = 0;
    cur.cursor = 0;
    cur.state = cur.lines.length ? "ready" : "empty";
    if (song.status !== "ready" || !song.media) {
      cur.audioState = "failed";
    } else if (!cur.audio) setupAudio(song);
    if (cur.lines.length && cur.startAt) {
      cur.sel = Math.max(0, M.lineAt(cur.lines, cur.startAt - cur.offset));
    }
  } catch (err) {
    if (ed !== cur) return;
    cur.state = "error";
    cur.loadError = err;
  }
  renderAll();
  if (cur.state === "ready") {
    focusFk("list");
    scrollToRow(cur.sel, "center");
  }
}

function setupAudio(song) {
  const mk = (src) => {
    const a = new Audio();
    a.preload = "auto";
    a.preservesPitch = true;
    a.src = src;
    return a;
  };
  const inst = mk(song.media.instrumental);
  const voc = mk(song.media.vocals);
  voc.muted = !ed.voice;
  inst.playbackRate = ed.rate;
  voc.playbackRate = ed.rate;
  ed.audio = { inst, voc, playing: false };
  ed.audioState = "loading";
  const cur = ed;
  inst.addEventListener("loadedmetadata", () => {
    if (ed !== cur) return;
    if (cur.startAt) seekAudio(Math.max(0, cur.startAt - 1000));
  }, { once: true });
  inst.addEventListener("canplay", () => {
    if (ed !== cur || cur.audioState === "ok") return;
    cur.audioState = "ok";
    renderWave();
  });
  inst.addEventListener("error", () => {
    if (ed !== cur) return;
    cur.audioState = "failed";
    renderWave();
  });
  inst.addEventListener("ended", () => {
    if (ed !== cur) return;
    pause();
  });
  // forma de onda da voz: não bloqueia o editor; > 4 s → barra simples (troca se terminar depois)
  const timer = setTimeout(() => {
    if (ed === cur && cur.waveState === "loading") {
      cur.waveState = "fallback";
      renderWave();
    }
  }, WAVE_TIMEOUT_MS);
  loadPeaks(song.media.vocals, cur.abort.signal).then((res) => {
    if (ed !== cur) return;
    cur.peaks = res.peaks;
    cur.waveState = "ok";
    renderWave();
  }).catch(() => {
    if (ed !== cur) return;
    cur.waveState = "fallback";
    renderWave();
  }).finally(() => clearTimeout(timer));
}

function dirty() {
  return ed && ed.state === "ready" && M.changeCount(ed.lines, ed.offset, ed.savedOffset) > 0;
}

function onBeforeUnload(e) {
  if (dirty()) {
    e.preventDefault();
    e.returnValue = "";
  }
}

function tryClose() {
  if (!ed) return;
  if (ed.saving) return;
  if (dirty()) confirmLeave();
  else closeEditor();
}

function closeEditor() {
  if (!ed) return;
  const cur = ed;
  ed = null;
  cancelAnimationFrame(cur.raf);
  cur.abort.abort();
  if (cur.audio) {
    for (const a of [cur.audio.inst, cur.audio.voc]) {
      a.pause();
      a.removeAttribute("src");
      a.load();
    }
  }
  removeEventListener("beforeunload", onBeforeUnload);
  cur.els.bg.remove();
  if (!document.querySelector("#modal-root .modal-bg")) document.body.classList.remove("has-modal");
  if (cur.lastFocus && cur.lastFocus.isConnected) cur.lastFocus.focus();
}

// ======================================================================== áudio

function clock() {
  if (!ed || !ed.audio) return ed && ed.startAt ? ed.startAt : 0;
  return ed.audio.inst.currentTime * 1000;
}

function durationMs() {
  if (!ed) return 0;
  const a = ed.audio && ed.audio.inst;
  if (a && Number.isFinite(a.duration) && a.duration > 0) return a.duration * 1000;
  return (ed.song && ed.song.duration_ms) || 0;
}

function canPlay() {
  return ed && ed.audio && ed.audioState === "ok";
}

function play() {
  if (!canPlay()) return;
  const { inst, voc } = ed.audio;
  voc.currentTime = inst.currentTime;
  const p = inst.play();
  voc.play().catch(() => {});
  if (p) p.catch(() => {});
  ed.audio.playing = true;
  syncPlayBtn();
}

function pause() {
  if (!ed || !ed.audio) return;
  ed.audio.inst.pause();
  ed.audio.voc.pause();
  ed.audio.playing = false;
  ed.countdown = 0;
  syncPlayBtn();
}

function togglePlay() {
  if (!ed || !ed.audio) return;
  if (ed.audio.playing) pause();
  else play();
}

function seekAudio(ms) {
  if (!ed || !ed.audio) return;
  const d = durationMs();
  const t = Math.max(0, d ? Math.min(ms, d - 50) : ms) / 1000;
  ed.audio.inst.currentTime = t;
  ed.audio.voc.currentTime = t;
  ed.drawn = null;
}

function setRate(r) {
  if (!RATES.includes(r)) return;
  ed.rate = r;
  lsSet(LS_RATE, r);
  if (ed.audio) {
    ed.audio.inst.playbackRate = r;
    ed.audio.voc.playbackRate = r;
  }
  keepFocus(renderTools);
}

function toggleVoice() {
  ed.voice = !ed.voice;
  if (ed.audio) ed.audio.voc.muted = !ed.voice;
  keepFocus(renderTools);
}

/** Toca a linha `i` a partir de 1 s antes (AC-09.3). */
function hearLine(i, lead = 1000) {
  const l = ed.lines[i];
  if (!l || l.start == null || !canPlay()) return;
  seekAudio(l.start + ed.offset - lead);
  play();
}

// ======================================================================== edição

function editable() {
  return ed && ed.state === "ready" && !ed.saving && !ed.job;
}

function record(key = null) {
  M.record(ed.hist, M.snapshot(ed.lines, ed.offset, ed.marked), key);
}

function afterEdit(rows = null) {
  if (rows) {
    for (const i of rows) renderRow(i);
    renderOrderFlags();
  } else renderList();
  renderHead();
  renderSideDynamic();
  ed.drawn = null;
  if (!ed.els.box.contains(document.activeElement)) ed.els.list.focus({ preventScroll: true });
}

/** Atualiza só os tempos exibidos (o offset mudou): lista, ajuste fino e rótulos. */
function refreshTimes() {
  if (ed.state !== "ready") return;
  for (const row of ed.els.list.querySelectorAll(".srow")) {
    const i = Number(row.dataset.i);
    const l = ed.lines[i];
    const txt = l.start == null ? "—:—.—" : fmtClock(l.start + ed.offset);
    const tv = row.querySelector(".tv");
    if (tv && tv.textContent !== txt) {
      tv.textContent = txt;
      row.setAttribute("aria-label", rowLabel(i));
    }
  }
  const fine = ed.els.list.querySelector(".fine output");
  const l = ed.lines[ed.sel];
  if (fine && l) fine.textContent = l.start == null ? "—" : fmtClock(l.start + ed.offset);
}

function selectLine(i, { scroll = true } = {}) {
  if (!ed.lines.length) return;
  const n = Math.max(0, Math.min(ed.lines.length - 1, i));
  const old = ed.sel;
  ed.sel = n;
  if (ed.mode === "mark") ed.cursor = n;
  renderRow(old);
  renderRow(n);
  ed.els.list.setAttribute("aria-activedescendant", `srow-${n}`);
  if (scroll) scrollToRow(n);
  if (ed.mode === "mark") renderSide();
  ed.drawn = null;
}

function fineShift(delta) {
  if (!editable()) return;
  const i = ed.sel;
  const l = ed.lines[i];
  if (!l || l.start == null) {
    say("Linha sem tempo: marque-a no modo Tocar e marcar.");
    return;
  }
  record(`fine:${i}:${ed.ripple}`);
  M.shiftLine(ed.lines, i, delta, { ripple: ed.ripple, marked: ed.marked });
  afterEdit(ed.ripple ? null : [i]);
  say(`Linha ${i + 1}: ${fmtClock(l.start + ed.offset)}`);
}

function setOffset(v, key = null) {
  if (!editable()) return;
  const nv = Math.max(-OFFSET_LIMIT, Math.min(OFFSET_LIMIT, Math.round(v)));
  if (nv === ed.offset) return;
  record(key);
  ed.offset = nv;
  refreshTimes();
  renderHead();
  renderOffsetValue();
  ed.drawn = null;
}

function doUndo() {
  if (!editable()) return;
  const snap = M.undo(ed.hist, M.snapshot(ed.lines, ed.offset, ed.marked));
  if (!snap) return;
  ed.offset = M.restore(ed.lines, ed.marked, snap);
  keepFocus(() => {
    renderList();
    renderHead();
    renderSide();
  });
  say("Desfeito");
}

function doRedo() {
  if (!editable()) return;
  const snap = M.redo(ed.hist, M.snapshot(ed.lines, ed.offset, ed.marked));
  if (!snap) return;
  ed.offset = M.restore(ed.lines, ed.marked, snap);
  keepFocus(() => {
    renderList();
    renderHead();
    renderSide();
  });
  say("Refeito");
}

function discardChanges() {
  for (const l of ed.lines) l.start = l.orig;
  ed.marked = ed.lines.map(() => false);
  ed.offset = ed.savedOffset;
  ed.hist = M.createHistory();
  ed.banner = null;
  keepFocus(renderAll);
  say("Alterações descartadas");
}

// ---------------------------------------------------------------- tocar e marcar

function setMode(mode) {
  if (ed.state !== "ready" || mode === ed.mode) return;
  if (mode === "mark" && !editable()) return;
  ed.mode = mode;
  if (mode === "mark") {
    const firstUntimed = ed.lines.findIndex((l) => l.start == null);
    ed.cursor = ed.sel || (firstUntimed >= 0 ? firstUntimed : 0);
    ed.sel = ed.cursor;
    if (ed.rate === 1) setRate(lsGet(LS_RATE + ".mark", 0.75));
    startCountdown();
  } else {
    ed.countdown = 0;
  }
  keepFocus(renderAll);
  scrollToRow(ed.sel, "center");
}

function cursorTime(i) {
  const l = ed.lines[i];
  if (l && l.start != null) return l.start + ed.offset;
  for (let j = i - 1; j >= 0; j--) {
    if (ed.lines[j].start != null) return ed.lines[j].start + ed.offset + 500;
  }
  return clock();
}

/** Entra no modo: toca de 3 s antes da linha do cursor com contagem "3·2·1" no botão. */
function startCountdown() {
  if (!canPlay()) return;
  seekAudio(cursorTime(ed.cursor) - 3000);
  ed.countdown = 3;
  play();
  const cur = ed;
  const tick = () => {
    if (ed !== cur || !cur.countdown) return;
    cur.countdown--;
    renderPad();
    if (cur.countdown > 0) setTimeout(tick, 1000 / cur.rate);
  };
  setTimeout(tick, 1000 / cur.rate);
}

function mark() {
  if (!editable() || ed.mode !== "mark") return;
  const i = ed.cursor;
  if (i >= ed.lines.length) return;
  const t = clock() - ed.react - ed.offset;
  record();
  M.markLine(ed.lines, ed.marked, i, t, { ripple: ed.ripple });
  ed.countdown = 0;
  ed.flash = i;
  const pad = ed.els.side.querySelector(".mark-pad");
  if (pad && !reducedMotion()) {
    pad.classList.add("hit");
    setTimeout(() => pad.classList.remove("hit"), 80);
  }
  say(`Linha ${i + 1} marcada em ${fmtClock(ed.lines[i].start + ed.offset).replace(".", ",")}`);
  if (i + 1 >= ed.lines.length) {
    ed.mode = "adjust";
    ed.sel = i;
    keepFocus(renderAll);
    toast("Todas as linhas marcadas — ouça para conferir", { level: "info" });
    return;
  }
  ed.cursor = i + 1;
  ed.sel = i + 1;
  afterEdit(ed.ripple ? null : [i, i + 1]);
  ed.els.list.setAttribute("aria-activedescendant", `srow-${ed.sel}`);
  renderPad();
  scrollToRow(ed.sel);
}

function backLine() {
  if (ed.mode !== "mark" || ed.cursor <= 0) return;
  ed.cursor--;
  ed.sel = ed.cursor;
  const l = ed.lines[ed.cursor];
  if (l.start != null && canPlay()) {
    seekAudio(l.start + ed.offset - 2000);
    if (!ed.audio.playing) play();
  }
  renderList();
  renderSide();
  scrollToRow(ed.sel);
  say(`Voltou para a linha ${ed.cursor + 1}`);
}

// ---------------------------------------------------------------- salvar

async function save({ closeAfter = false } = {}) {
  if (!ed || ed.saving || ed.state !== "ready" || !dirty()) return;
  const cur = ed;
  const timing = M.timingChanged(cur.lines);
  if (timing) {
    const v = M.validateLines(cur.lines, durationMs());
    if (v) {
      cur.banner = { level: "warning", msg: validationMessage(v), retry: false };
      if (cur.mode === "mark") cur.mode = "adjust";
      cur.sel = v.line;
      keepFocus(renderAll);
      scrollToRow(v.line, "center");
      return;
    }
  }
  cur.saving = true;
  cur.banner = null;
  pause();
  keepFocus(renderAll);
  const id = encodeURIComponent(cur.songId);
  try {
    if (timing) {
      const doc = await api.put(
        `/api/songs/${id}/lyrics/manual-timing`,
        M.timingPayload(cur.lines, cur.candId),
      );
      if (ed !== cur) return;
      cur.candId = doc.candidate_id || cur.candId;
      cur.cand = { id: cur.candId, source: "manual", label: "Manual" };
      for (const l of cur.lines) l.orig = l.start;
      const fresh = M.linesFromDoc(doc);
      if (fresh.length === cur.lines.length) {
        fresh.forEach((f, i) => {
          cur.lines[i].end = f.end;
          cur.lines[i].words = f.words;
          cur.lines[i].orig = f.start;
          cur.lines[i].start = f.start;
          cur.lines[i].interpolated = false;
        });
      }
    }
    if (cur.offset !== cur.savedOffset) {
      const s = await api.patch(`/api/songs/${id}`, { offset_ms: cur.offset });
      if (ed !== cur) return;
      cur.song = { ...cur.song, ...s };
      cur.savedOffset = cur.offset;
    }
    cur.marked = cur.lines.map(() => false);
    cur.hist = M.createHistory();
    cur.saving = false;
    toast(
      timing ? "Sincronia salva — a original continua em Letra › Candidatas" : "Ajuste geral da letra salvo",
    );
    if (closeAfter) {
      closeEditor();
      return;
    }
    keepFocus(renderAll);
  } catch (err) {
    if (ed !== cur) return;
    cur.saving = false;
    if (err.code === "invalid_timing" && err.details && Number.isInteger(err.details.line)) {
      cur.sel = Math.min(cur.lines.length - 1, err.details.line);
      cur.banner = {
        level: "danger",
        msg: `Não deu para salvar: linha ${cur.sel + 1} — ${err.message} Suas alterações continuam aqui.`,
        retry: false,
      };
      if (cur.mode === "mark") cur.mode = "adjust";
      keepFocus(renderAll);
      scrollToRow(cur.sel, "center");
    } else {
      cur.banner = {
        level: "danger",
        msg: `Não deu para salvar: ${err.message} Suas alterações continuam aqui.`,
        retry: true,
      };
      keepFocus(renderAll);
    }
  }
}

function validationMessage(v) {
  if (v.reason === "untimed") {
    return `${v.count} ${
      v.count === 1 ? "linha está" : "linhas estão"
    } sem tempo. Marque todas no modo Tocar e marcar antes de salvar.`;
  }
  if (v.reason === "not_sorted") {
    return `${v.count} ${
      v.count === 1 ? "linha está" : "linhas estão"
    } fora de ordem (começa antes da anterior). Selecionei a primeira: ajuste e salve de novo.`;
  }
  if (v.reason === "after_end") return `A linha ${v.line + 1} começa depois do fim da música.`;
  return "Não há linhas para salvar.";
}

// ---------------------------------------------------------------- detectar offset

async function detectOffset() {
  if (!editable() || (ed.detect && ed.detect.state === "busy")) return;
  const cur = ed;
  cur.detect = { state: "busy" };
  keepFocus(renderSide);
  try {
    const body = cur.candId ? { candidate_id: cur.candId } : {};
    const res = await api.post(`/api/songs/${encodeURIComponent(cur.songId)}/lyrics/detect-offset`, body);
    if (ed !== cur) return;
    if (res && res.job) {
      cur.detect = { state: "busy", jobId: res.job.id };
      return; // resultado chega por `lyrics.offset_detected`
    }
    showDetect(res);
  } catch (err) {
    if (ed !== cur) return;
    cur.detect = { state: "fail", msg: err.code === "no_lyrics" ? "Esta música ainda não tem letra." : null };
    keepFocus(renderSide);
  }
}

function showDetect(res) {
  const conf = Number(res.confidence) || 0;
  const drift = Math.abs(Number(res.drift_ms_per_min) || 0);
  ed.detect = {
    state: "ok",
    value: Math.round(res.suggested_offset_ms || 0),
    conf,
    drift,
    low: conf < 0.4,
    drifting: drift > DRIFT_HIGH,
  };
  keepFocus(renderSide);
  say(`Sugestão de ajuste: ${fmtOffset(ed.detect.value)}`);
}

/** Evento `lyrics.offset_detected` (fim do job `analyze`). */
export function onOffsetDetected(data) {
  if (!ed || !data || data.song_id !== ed.songId) return;
  if (!ed.detect || ed.detect.state !== "busy") return;
  showDetect(data);
}

// ======================================================================== eventos externos

/** `job.updated`: job de letra desta música (banner + bloqueio) ou erro do `analyze`. */
export function onEditorJobEvent(job) {
  if (!ed || !job || job.song_id !== ed.songId) return;
  if (job.kind === "analyze" && ed.detect && ed.detect.state === "busy" && ed.detect.jobId === job.id) {
    if (job.stage === "error" || job.stage === "canceled") {
      ed.detect = { state: "fail", msg: null };
      keepFocus(renderSide);
    }
    return;
  }
  if (job.kind !== "align" && job.kind !== "transcribe") return;
  const active = activeLyricsJob(ed.songId);
  const was = ed.job;
  ed.job = active;
  if (!active && was && job.stage === "error") {
    ed.banner = {
      level: "danger",
      msg: `Não deu para sincronizar: ${(job.error && job.error.message) || "a tarefa falhou."}`,
      retry: false,
    };
  }
  if (Boolean(was) !== Boolean(active) || active) keepFocus(renderAll);
}

/** `lyrics.updated`: recarrega se a letra selecionada mudou por fora (e não há alterações a perder). */
export function onEditorLyricsEvent(data) {
  if (!ed || !data || data.song_id !== ed.songId) return;
  if (data.candidate_id && data.candidate_id === ed.candId) return;
  if (ed.saving) return; // a resposta do próprio Salvar pode chegar depois do evento
  if (dirty()) {
    ed.banner = {
      level: "info",
      msg: "A letra desta música mudou em outro lugar. Salve ou descarte para ver a nova.",
      retry: false,
    };
    keepFocus(renderBanner);
    return;
  }
  ed.mode = "adjust";
  ed.banner = null;
  ed.job = activeLyricsJob(ed.songId);
  load();
}

/** `song.updated`: offset mudou por fora (ex.: `[`/`]` no Palco) e não há edição local de offset. */
export function onEditorSongEvent(song) {
  if (!ed || !song || song.id !== ed.songId || !ed.song) return;
  ed.song = { ...ed.song, ...song };
  if (typeof song.offset_ms === "number" && ed.offset === ed.savedOffset && !ed.saving) {
    ed.offset = song.offset_ms;
    ed.savedOffset = song.offset_ms;
    refreshTimes();
    renderOffsetValue();
  }
}

// ======================================================================== render

function keepFocus(fn) {
  if (!ed) return;
  const a = document.activeElement;
  const fk = a && a.dataset ? a.dataset.fk : null;
  fn();
  if (!ed) return;
  if (fk) {
    const n = ed.els.box.querySelector(`[data-fk="${CSS.escape(fk)}"]`);
    if (n && !n.disabled) n.focus({ preventScroll: true });
    else if (!ed.els.box.contains(document.activeElement)) ed.els.list.focus({ preventScroll: true });
  } else if (a && !a.isConnected) ed.els.list.focus({ preventScroll: true });
}

function focusFk(fk) {
  const n = ed && ed.els.box.querySelector(`[data-fk="${CSS.escape(fk)}"]`);
  if (n) n.focus({ preventScroll: true });
}

function say(text) {
  const l = ed && ed.els.live;
  if (!l) return;
  l.textContent = "";
  setTimeout(() => {
    if (l.isConnected) l.textContent = text;
  }, 30);
}

function renderAll() {
  if (!ed) return;
  ed.els.box.classList.toggle("mode-mark", ed.mode === "mark");
  renderHead();
  renderBanner();
  renderTools();
  renderWave();
  renderList();
  renderSide();
  renderLegend();
}

function btn(
  label,
  { fk, cls = "btn-ghost", iconId = null, onclick, disabled = false, title = null, aria = null },
) {
  return h(
    "button",
    {
      class: ["btn", cls],
      type: "button",
      "data-fk": fk,
      onclick,
      disabled,
      title,
      "aria-label": aria,
    },
    iconId ? icon(iconId, label ? "icon-sm" : "") : null,
    label,
  );
}

function renderHead() {
  const n = ed.state === "ready" ? M.changeCount(ed.lines, ed.offset, ed.savedOffset) : 0;
  const song = ed.song;
  const sub = [];
  if (song) sub.push(h("span", {}, [song.title, song.artist].filter(Boolean).join(" · ")));
  if (ed.cand) sub.push(lyricsSourceChip(ed.cand.source, { label: ed.cand.label }));
  else if (ed.state !== "loading") sub.push(lyricsSourceChip(null));
  const busy = ed.saving || Boolean(ed.job);
  replace(
    ed.els.head,
    btn(null, {
      fk: "close",
      iconId: "x",
      cls: "btn-ghost btn-icon",
      onclick: tryClose,
      aria: "Fechar editor (Esc)",
      title: "Fechar (Esc)",
    }),
    h(
      "div",
      { class: "ttl" },
      h("h2", { id: "ed-title" }, "Editar sincronia"),
      h("div", { class: "sub" }, sub),
    ),
    h(
      "div",
      { class: "acts" },
      h(
        "span",
        { class: "unsaved", "aria-live": "polite", hidden: !n },
        n ? `${n} ${n === 1 ? "alteração não salva" : "alterações não salvas"}` : "",
      ),
      btn(null, {
        fk: "undo",
        iconId: "undo",
        cls: "btn-ghost btn-icon",
        onclick: doUndo,
        disabled: !ed.hist.undo.length || busy,
        aria: "Desfazer (Ctrl+Z)",
        title: "Desfazer (Ctrl+Z)",
      }),
      btn(null, {
        fk: "redo",
        iconId: "redo",
        cls: "btn-ghost btn-icon",
        onclick: doRedo,
        disabled: !ed.hist.redo.length || busy,
        aria: "Refazer (Ctrl+Shift+Z)",
        title: "Refazer (Ctrl+Shift+Z)",
      }),
      btn("Descartar", { fk: "discard", onclick: confirmDiscard, disabled: !n || busy }),
      h(
        "button",
        {
          class: ["btn", "btn-primary", ed.saving && "is-loading"],
          type: "button",
          "data-fk": "save",
          "aria-keyshortcuts": "Control+S",
          title: "Salvar (Ctrl+S)",
          "aria-busy": ed.saving ? "true" : null,
          disabled: !n || busy,
          onclick: () => save(),
        },
        icon(ed.saving ? "loader" : "check", "icon-sm"),
        ed.saving ? "Salvando…" : "Salvar",
      ),
    ),
  );
}

function renderBanner() {
  const nodes = [];
  if (ed.job) {
    nodes.push(
      h(
        "div",
        { class: "banner info ed-banner", role: "status" },
        icon("wave"),
        h(
          "span",
          { class: "grow" },
          h("b", {}, `${lyricsJobLabel(ed.job)}…`),
          " — a edição fica bloqueada e o editor recarrega sozinho quando terminar.",
        ),
      ),
    );
  } else if (ed.banner) {
    const b = ed.banner;
    nodes.push(
      h(
        "div",
        { class: ["banner", b.level, "ed-banner"], role: b.level === "info" ? "status" : "alert" },
        icon(b.level === "info" ? "lyrics" : "alert"),
        h("span", { class: "grow" }, b.msg),
        b.retry
          ? btn("Tentar de novo", {
            fk: "retry",
            cls: "btn-primary",
            iconId: "refresh",
            onclick: () => save(),
          })
          : null,
        btn(null, {
          fk: "bclose",
          iconId: "x",
          cls: "btn-ghost btn-icon",
          aria: "Fechar aviso",
          onclick: () => {
            ed.banner = null;
            renderBanner();
            ed.els.list.focus();
          },
        }),
      ),
    );
  }
  replace(ed.els.banner, nodes);
  ed.els.box.setAttribute("aria-busy", String(Boolean(ed.job || ed.saving || ed.state === "loading")));
}

function segmented(label, items, current, onpick, fkPrefix, cls = "") {
  const group = h(
    "div",
    { class: ["seg", cls], role: "radiogroup", "aria-label": label },
    items.map(([value, text, iconId]) =>
      h(
        "button",
        {
          type: "button",
          role: "radio",
          "data-fk": `${fkPrefix}${value}`,
          "aria-checked": String(current === value),
          tabindex: current === value ? "0" : "-1",
          onclick: () => onpick(value),
        },
        iconId ? icon(iconId, "icon-sm") : null,
        text,
      )
    ),
  );
  group.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    e.stopPropagation();
    const i = items.findIndex(([v]) => v === current);
    const next = items[(i + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length][0];
    onpick(next);
    focusFk(`${fkPrefix}${next}`);
  });
  return group;
}

function renderTools() {
  const ready = ed.state === "ready";
  replace(
    ed.els.tools,
    ready
      ? segmented(
        "Modo do editor (M)",
        [["adjust", "Ajustar", "sliders"], ["mark", "Tocar e marcar", "target"]],
        ed.mode,
        setMode,
        "mode-",
        "lg",
      )
      : null,
    h("span", { class: "spacer" }),
    h(
      "div",
      { class: "ed-rate" },
      h("span", { class: "lbl", id: "ed-rate-l" }, "Velocidade"),
      segmented(
        "Velocidade de reprodução (1, 2, 3)",
        [[0.5, "0,5×"], [0.75, "0,75×"], [1, "1×"]],
        ed.rate,
        (r) => {
          setRate(r);
          if (ed.mode === "mark") lsSet(LS_RATE + ".mark", r);
        },
        "rate-",
      ),
    ),
    h(
      "button",
      {
        class: "btn btn-ghost toggle",
        type: "button",
        "data-fk": "voice",
        "aria-pressed": String(ed.voice),
        title: "Ouvir a voz do cantor no editor (não muda o Palco)",
        onclick: toggleVoice,
      },
      icon("mic", "icon-sm"),
      "Voz",
    ),
    h(
      "button",
      {
        class: "btn btn-ghost toggle",
        type: "button",
        "data-fk": "ripple",
        "aria-pressed": String(ed.ripple),
        title: "Ao mover uma linha, mover junto as seguintes ainda não marcadas (R)",
        onclick: () => {
          ed.ripple = !ed.ripple;
          keepFocus(renderTools);
        },
      },
      icon("ripple", "icon-sm"),
      "Mover as seguintes",
    ),
  );
}

function renderWave() {
  const box = ed.els.wave;
  const playable = canPlay();
  if (ed.audioState === "failed") {
    replace(
      box,
      h(
        "div",
        { class: "callout danger", role: "alert" },
        icon("alert"),
        h(
          "div",
          {},
          "Não deu para tocar o áudio desta música. Reprocesse em Biblioteca › Letra. A lista continua editável à mão.",
        ),
      ),
    );
    ed.els.overview = null;
    ed.els.canvas = null;
    return;
  }
  const loadingWave = ed.waveState === "loading";
  if (!box.querySelector(".ed-trans")) {
    ed.els.overview = h("canvas", {
      class: "ed-overview",
      role: "img",
      "aria-label": "Visão geral da música inteira; clique para ir ao ponto",
    });
    ed.els.canvas = h("canvas", {
      class: "ed-wave-c",
      tabindex: "0",
      role: "slider",
      "data-fk": "wave",
      "aria-label": "Posição na forma de onda da voz (←/→ movem 1 s)",
      "aria-valuemin": "0",
    });
    ed.els.waveHint = h("p", { class: "hint ed-wave-hint" });
    ed.els.overview.addEventListener("click", (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      seekAudio(((e.clientX - r.left) / r.width) * durationMs());
    });
    ed.els.canvas.addEventListener("click", (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      seekAudio(clock() - WINDOW_MS / 2 + ((e.clientX - r.left) / r.width) * WINDOW_MS);
    });
    ed.els.canvas.addEventListener("keydown", (e) => {
      if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && !e.altKey && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        seekAudio(clock() + (e.key === "ArrowRight" ? 1000 : -1000));
      }
    });
    ed.els.play = h("button", {
      class: "btn btn-play",
      type: "button",
      "data-fk": "play",
      onclick: togglePlay,
    });
    ed.els.time = h("b", { class: "tnum" }, "0:00.00");
    ed.els.dur = h("span", { class: "tnum" }, "0:00");
    replace(
      box,
      ed.els.overview,
      ed.els.canvas,
      ed.els.waveHint,
      h(
        "div",
        { class: "ed-trans" },
        btn(null, {
          fk: "prevline",
          iconId: "prev",
          cls: "btn-ghost btn-icon btn-round",
          aria: "Ir para a linha anterior",
          title: "Linha anterior",
          onclick: () => jumpLine(-1),
        }),
        ed.els.play,
        btn(null, {
          fk: "nextline",
          iconId: "next",
          cls: "btn-ghost btn-icon btn-round",
          aria: "Ir para a próxima linha",
          title: "Próxima linha",
          onclick: () => jumpLine(1),
        }),
        h("span", { class: "time" }, ed.els.time, " / ", ed.els.dur),
        btn("−5 s", {
          fk: "m5",
          cls: "btn-ghost btn-sm",
          aria: "Voltar 5 segundos (Alt+←)",
          onclick: () => seekAudio(clock() - 5000),
        }),
        btn("+5 s", {
          fk: "p5",
          cls: "btn-ghost btn-sm",
          aria: "Avançar 5 segundos (Alt+→)",
          onclick: () => seekAudio(clock() + 5000),
        }),
      ),
    );
  }
  ed.els.overview.classList.toggle("sk", loadingWave);
  ed.els.canvas.classList.toggle("sk", loadingWave);
  ed.els.waveHint.textContent = loadingWave
    ? "Preparando a forma de onda da voz…"
    : ed.waveState === "fallback"
    ? "Forma de onda indisponível — usando a barra de tempo."
    : "";
  ed.els.waveHint.hidden = !ed.els.waveHint.textContent;
  for (const b of box.querySelectorAll(".ed-trans button")) b.disabled = !playable;
  ed.els.canvas.setAttribute("aria-valuemax", String(Math.round(durationMs())));
  ed.els.dur.textContent = fmtTime(durationMs());
  syncPlayBtn();
  ed.drawn = null;
}

function syncPlayBtn() {
  const b = ed && ed.els.play;
  if (!b) return;
  const playing = Boolean(ed.audio && ed.audio.playing);
  const key = playing ? "pause" : "play";
  if (b.dataset.icon !== key) {
    replace(b, icon(key, "icon-lg"));
    b.dataset.icon = key;
  }
  const label = ed.audioState === "loading"
    ? "Carregando o áudio…"
    : playing
    ? "Pausar (Espaço ou K)"
    : "Tocar (Espaço ou K)";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.disabled = !canPlay();
}

function jumpLine(dir) {
  if (!ed.lines.length) return;
  const cur = M.lineAt(ed.lines, clock() - ed.offset + 1000);
  let i = cur + dir;
  while (i >= 0 && i < ed.lines.length && ed.lines[i].start == null) i += dir;
  if (i < 0 || i >= ed.lines.length) return;
  selectLine(i);
  hearLine(i);
}

function renderList() {
  const list = ed.els.list;
  if (ed.state === "ready") list.setAttribute("role", "listbox");
  else {
    list.removeAttribute("role");
    list.removeAttribute("aria-activedescendant");
  }
  if (ed.state === "loading") {
    replace(ed.els.listH, "Linhas");
    replace(
      list,
      Array.from({ length: 8 }, (_, k) => h("div", { class: ["sk", "skrow", `skrow-${k % 4}`] })),
    );
    list.setAttribute("aria-busy", "true");
    return;
  }
  list.removeAttribute("aria-busy");
  if (ed.state === "error") {
    replace(ed.els.listH, "Linhas");
    replace(
      list,
      emptyState({
        iconId: "alert",
        danger: true,
        compact: true,
        title: "Não foi possível abrir a letra",
        text: ed.loadError ? ed.loadError.message : "",
        actions: [
          btn("Tentar de novo", { fk: "reload", cls: "btn-primary", iconId: "refresh", onclick: load }),
        ],
      }),
    );
    return;
  }
  if (ed.state === "empty") {
    replace(ed.els.listH, "Linhas · 0");
    replace(list, emptyView());
    return;
  }
  const mark = ed.mode === "mark";
  replace(
    ed.els.listH,
    `Linhas · ${ed.lines.length}`,
    h("span", { class: "spacer" }),
    h("span", { class: "ed-list-tip" }, mark ? "▸ = próxima a marcar" : "Clique numa linha para ouvir"),
  );
  const rows = [];
  ed.lines.forEach((_, i) => {
    rows.push(rowEl(i));
  });
  replace(list, rows);
  renderOrderFlags();
  list.setAttribute("aria-activedescendant", `srow-${mark ? ed.cursor : ed.sel}`);
  ed.playIdx = -2;
  ed.drawn = null;
}

function rowEl(i) {
  const l = ed.lines[i];
  const mark = ed.mode === "mark";
  const sel = i === ed.sel;
  const eff = l.start == null ? null : l.start + ed.offset;
  const changed = l.start !== l.orig;
  const before = l.orig == null ? "sem tempo" : fmtClock(l.orig + ed.savedOffset);
  const row = h(
    "div",
    {
      class: [
        "srow",
        sel && "sel",
        mark && i === ed.cursor && "cursor",
        l.interpolated && !changed && "interp",
        eff == null && "untimed",
        ed.flash === i && !reducedMotion() && "flash",
      ],
      id: `srow-${i}`,
      role: "option",
      "aria-selected": String(sel),
      dataset: { i: String(i) },
    },
    h("span", { class: "n" }, String(i + 1)),
    h("span", { class: "st", "aria-hidden": "true" }),
    h(
      "span",
      { class: "tm" },
      h("span", { class: ["dot", !changed && "off"], title: changed ? `Alterada — antes ${before}` : null }),
      l.interpolated && !changed
        ? h("span", { title: "Tempo estimado entre as linhas vizinhas" }, "≈")
        : null,
      h("span", { class: "tv" }, eff == null ? "—:—.—" : fmtClock(eff)),
      h(
        "span",
        { class: "warn-ico", title: "Começa antes da linha anterior", hidden: true },
        icon("alert", "icon-sm"),
      ),
    ),
    h("span", { class: "tx", title: l.text }, l.text),
  );
  if (ed.flash === i) ed.flash = -1;
  row.setAttribute("aria-label", rowLabel(i));
  if (!sel || mark) return row;
  return [row, fineEl(i)];
}

function fineEl(i) {
  const l = ed.lines[i];
  const eff = l.start == null ? null : l.start + ed.offset;
  const off = !editable() || l.start == null;
  const b = (d, text, aria) =>
    h("button", {
      class: "btn btn-secondary",
      type: "button",
      tabindex: "-1",
      "aria-label": `Linha ${i + 1} ${aria}`,
      title: aria,
      disabled: off,
      onclick: (e) => {
        e.stopPropagation();
        fineShift(d);
      },
    }, text);
  return h(
    "div",
    {
      class: "fine",
      role: "group",
      "aria-label": `Ajuste fino da linha ${i + 1} (←/→ e Shift+←/→)`,
      id: `fine-${i}`,
    },
    b(-100, "−100", "cem milissegundos mais cedo (Shift+←)"),
    b(-10, "−10", "dez milissegundos mais cedo (←)"),
    h("output", { class: "tnum" }, eff == null ? "—" : fmtClock(eff)),
    b(10, "+10", "dez milissegundos mais tarde (→)"),
    b(100, "+100", "cem milissegundos mais tarde (Shift+→)"),
    h("span", { class: "gap" }),
    h(
      "button",
      {
        class: "btn btn-ghost",
        type: "button",
        tabindex: "-1",
        disabled: !canPlay() || l.start == null,
        onclick: (e) => {
          e.stopPropagation();
          hearLine(i);
        },
      },
      icon("play", "icon-sm"),
      "Ouvir (Enter)",
    ),
  );
}

function rowLabel(i) {
  const l = ed.lines[i];
  const changed = l.start !== l.orig;
  const time = l.start == null ? "sem tempo" : fmtClock(l.start + ed.offset);
  return `Linha ${i + 1}, ${time}${l.interpolated && !changed ? " (estimado)" : ""}${
    changed ? ", alterada" : ""
  }: ${l.text}`;
}

/** Re-renderiza só a linha `i` (e o ajuste fino, se for a selecionada). */
function renderRow(i) {
  if (!ed || ed.state !== "ready" || i < 0 || i >= ed.lines.length) return;
  const old = document.getElementById(`srow-${i}`);
  if (!old) return;
  const fine = document.getElementById(`fine-${i}`);
  if (fine) fine.remove();
  const fresh = rowEl(i);
  old.replaceWith(...(Array.isArray(fresh) ? fresh : [fresh]));
  ed.playIdx = -2;
}

/** Marca as linhas fora de ordem (⚠ + cor de aviso). */
function renderOrderFlags() {
  const bad = new Set(M.outOfOrder(ed.lines));
  for (const row of ed.els.list.querySelectorAll(".srow")) {
    const i = Number(row.dataset.i);
    const on = bad.has(i);
    row.classList.toggle("order", on);
    const w = row.querySelector(".warn-ico");
    if (w) w.hidden = !on;
  }
}

function scrollToRow(i, block = "nearest") {
  const row = document.getElementById(`srow-${i}`);
  if (row) row.scrollIntoView({ block, behavior: reducedMotion() ? "auto" : "smooth" });
}

function onListClick(e) {
  const row = e.target.closest(".srow");
  if (!row || !ed || ed.state !== "ready") return;
  const i = Number(row.dataset.i);
  if (ed.mode === "mark") {
    ed.cursor = i;
  }
  selectLine(i, { scroll: false });
  hearLine(i);
  ed.els.list.focus({ preventScroll: true });
}

function emptyView() {
  const area = h("textarea", {
    class: "textarea",
    id: "ed-paste",
    spellcheck: "false",
    "aria-describedby": "ed-paste-hint",
    placeholder: "Primeira linha da música\nSegunda linha…",
  });
  const hint = h("span", { class: "hint", id: "ed-paste-hint" }, "Uma linha por verso, com ou sem tempos.");
  const alignBtn = btn("Sincronizar pela voz", {
    fk: "paste-align",
    cls: "btn-primary",
    iconId: "wave",
    disabled: true,
    onclick: async (e) => {
      const b = e.currentTarget;
      b.disabled = true;
      try {
        const r = await submitPastedLyrics(ed.songId, area.value);
        if (ed && r.job) {
          ed.job = activeLyricsJob(ed.songId) || r.job;
          keepFocus(renderAll);
        }
      } catch (err) {
        if (b.isConnected) b.disabled = false;
        hint.classList.add("err");
        hint.textContent = err.message;
      }
    },
  });
  const markBtn = btn("Marcar eu mesmo no ritmo", {
    fk: "paste-mark",
    cls: "btn-secondary",
    iconId: "target",
    disabled: true,
    onclick: () => {
      const lines = M.linesFromText(area.value);
      if (!lines.length) return;
      ed.lines = lines;
      ed.marked = lines.map(() => false);
      ed.hist = M.createHistory();
      ed.sel = 0;
      ed.cursor = 0;
      ed.state = "ready";
      ed.cand = null;
      renderAll();
      setMode("mark");
      ed.els.list.focus();
    },
  });
  area.addEventListener("input", () => {
    const v = analyzePasted(area.value);
    hint.classList.toggle("err", Boolean(v.error));
    hint.textContent = pasteHint(v);
    alignBtn.disabled = !v.ok;
    markBtn.disabled = !v.ok || v.kind !== "plain";
  });
  const pasteBox = h(
    "div",
    { class: "field ed-paste", hidden: !ed.pasteOpen },
    h("label", { for: "ed-paste" }, "Letra oficial"),
    area,
    hint,
    h("div", { class: "ed-paste-acts" }, markBtn, alignBtn),
  );
  const busy = Boolean(ed.job);
  return h(
    "div",
    { class: "ed-empty" },
    emptyState({
      iconId: "lyrics",
      title: "Esta música ainda não tem letra",
      text:
        "Busque a letra oficial e sincronize pela voz, ou cole a letra (sem tempos) e marque no ritmo com " +
        "Tocar e marcar.",
      actions: [
        btn("Buscar letra oficial e sincronizar", {
          fk: "e-align",
          cls: "btn-primary",
          iconId: "wave",
          disabled: busy,
          onclick: (e) => runAlign(e.currentTarget),
        }),
        btn("Colar a letra", {
          fk: "e-paste",
          cls: "btn-secondary",
          iconId: "edit",
          onclick: () => {
            ed.pasteOpen = true;
            pasteBox.hidden = false;
            area.focus();
          },
        }),
        btn("Candidatas…", {
          fk: "e-cands",
          onclick: () => {
            const id = ed.songId;
            closeEditor();
            openLyrics(id);
          },
        }),
      ],
    }),
    pasteBox,
  );
}

async function runAlign(button) {
  if (!ed || ed.job) return;
  const cur = ed;
  const job = await alignLyrics(cur.songId, { btn: button, quiet: true });
  if (ed !== cur || !job) return;
  cur.job = activeLyricsJob(cur.songId) || job;
  cur.banner = null;
  keepFocus(renderAll);
}

function renderSide() {
  const side = ed.els.side;
  if (ed.state === "loading") {
    replace(side, h("div", { class: "sk sk-pv" }), h("div", { class: "sk sk-box" }));
    return;
  }
  if (ed.state !== "ready") {
    replace(side);
    return;
  }
  const parts = [];
  const tip = sourceTip();
  if (tip) parts.push(tip);
  ed.els.pv = h("div", { class: "pv", "aria-hidden": "true" });
  ed.pvKey = null;
  parts.push(h("div", { class: "pv-sec" }, h("h3", { class: "ed-sec-h" }, "Prévia no Palco"), ed.els.pv));
  if (ed.mode === "mark") parts.push(markPanel());
  else parts.push(offsetPanel());
  replace(side, parts);
}

/** Partes da coluna direita que mudam com edições (sem refazer o painel inteiro). */
function renderSideDynamic() {
  if (ed.mode === "mark") renderPad();
}

function sourceTip() {
  const src = ed.cand && ed.cand.source;
  const dis = Boolean(ed.job) || dirty();
  const why = dirty() ? "Salve ou descarte as alterações antes" : null;
  if (src === "lrclib") {
    return h(
      "div",
      { class: "callout info" },
      icon("wave"),
      h(
        "div",
        {},
        "Esta letra ainda não foi acertada pela voz. ",
        h("b", {}, "Buscar letra oficial e sincronizar"),
        " resolve sozinho em ~20 s.",
        h(
          "div",
          { class: "callout-acts" },
          btn("Buscar letra oficial e sincronizar", {
            fk: "tip-align",
            cls: "btn-secondary btn-sm",
            iconId: "wave",
            disabled: dis,
            title: why,
            onclick: (e) => runAlign(e.currentTarget),
          }),
        ),
      ),
    );
  }
  if (src === "whisper") {
    return h(
      "div",
      { class: "callout warning" },
      icon("alert"),
      h(
        "div",
        {},
        h("b", {}, "Extraída da voz (sem letra oficial)."),
        " Pode ter palavras erradas. Cole a letra oficial: o texto dela entra no lugar e os tempos vêm da voz.",
        h(
          "div",
          { class: "callout-acts" },
          btn("Colar letra oficial", {
            fk: "tip-paste",
            cls: "btn-secondary btn-sm",
            iconId: "edit",
            disabled: dis,
            title: why,
            onclick: () => pasteInner(),
          }),
        ),
      ),
    );
  }
  return null;
}

function offsetPanel() {
  const dis = !editable();
  const range = h("input", {
    type: "range",
    class: "guide",
    id: "ed-off",
    "data-fk": "orange",
    min: String(-OFFSET_LIMIT),
    max: String(OFFSET_LIMIT),
    step: "10",
    value: String(ed.offset),
    "aria-labelledby": "ed-off-h",
    "aria-valuetext": fmtOffset(ed.offset),
    disabled: dis,
  });
  range.addEventListener("input", () => setOffset(Number(range.value), "offset-slider"));
  ed.els.offRange = range;
  ed.els.offOut = h("output", { class: "tnum", for: "ed-off" }, fmtOffset(ed.offset));
  ed.els.zero = btn("Zerar", { fk: "ozero", onclick: () => setOffset(0), disabled: dis || !ed.offset });
  fillRange(range);
  const d = ed.detect;
  const busy = d && d.state === "busy";
  return h(
    "div",
    { class: "offset-box" },
    h("h3", { class: "ed-sec-h", id: "ed-off-h" }, "Ajuste geral (todas as linhas)"),
    h(
      "div",
      { class: "off-row" },
      btn("−", {
        fk: "ominus",
        cls: "btn-secondary",
        aria: "Letra 50 ms mais cedo ( [ )",
        title: "Mais cedo ( [ )",
        disabled: dis,
        onclick: () => setOffset(ed.offset - 50, "offset-btn"),
      }),
      range,
      btn("+", {
        fk: "oplus",
        cls: "btn-secondary",
        aria: "Letra 50 ms mais tarde ( ] )",
        title: "Mais tarde ( ] )",
        disabled: dis,
        onclick: () => setOffset(ed.offset + 50, "offset-btn"),
      }),
      ed.els.offOut,
    ),
    h(
      "p",
      { class: "hint flush" },
      "Letra atrasada? Puxe para −. Adiantada? Para +. A lista e a prévia mudam na hora.",
    ),
    h(
      "div",
      { class: "off-acts" },
      h(
        "button",
        {
          class: ["btn", "btn-secondary", busy && "is-loading"],
          type: "button",
          "data-fk": "detect",
          disabled: dis || busy,
          "aria-busy": busy ? "true" : null,
          onclick: detectOffset,
        },
        icon(busy ? "loader" : "wand", "icon-sm"),
        busy ? "Ouvindo a voz…" : "Detectar automaticamente",
      ),
      ed.els.zero,
    ),
    detectResult(),
  );
}

function detectResult() {
  const d = ed.detect;
  if (!d || d.state === "busy") return null;
  if (d.state === "fail") {
    return h(
      "div",
      { class: "callout danger sugg", role: "alert" },
      h(
        "div",
        {},
        d.msg || "Não deu para detectar: a voz está baixa ou a letra é de outra versão. Tente ",
        d.msg ? null : h("b", {}, "Buscar letra oficial e sincronizar"),
        d.msg ? null : " ou marque as linhas.",
      ),
      h(
        "div",
        { class: "row" },
        d.msg ? null : alignSuggestBtn(),
        btn("Fechar", { fk: "dclose", cls: "btn-ghost btn-sm", onclick: dismissDetect }),
      ),
    );
  }
  const warn = d.low || d.drifting;
  const conf = d.conf >= 0.7 ? "confiança alta" : d.conf >= 0.4 ? "confiança média" : "confiança baixa";
  return h(
    "div",
    { class: ["callout", warn ? "warning" : "info", "sugg"], role: "status" },
    h(
      "div",
      {},
      warn ? "Sugestão incerta: " : "Sugestão: ",
      h("b", { class: "tnum" }, fmtOffset(d.value)),
      ` · ${conf}`,
      warn ? ". Ouça antes de salvar." : "",
    ),
    d.drifting
      ? h(
        "div",
        { class: "hint flush" },
        `A letra escorrega ao longo da música (≈ ${
          Math.round(d.drift)
        } ms por minuto): um ajuste geral não ` +
          "resolve. Recomendado: ",
        h("b", {}, "Buscar letra oficial e sincronizar"),
        ".",
      )
      : d.low
      ? h("div", { class: "hint flush" }, "Se não bater, prefira Buscar letra oficial e sincronizar.")
      : null,
    h(
      "div",
      { class: "row" },
      btn("Aplicar", {
        fk: "dapply",
        cls: warn ? "btn-secondary btn-sm" : "btn-primary btn-sm",
        onclick: () => {
          const v = d.value;
          ed.detect = null;
          setOffset(v);
          keepFocus(renderSide);
          focusFk("detect");
          say(`Ajuste geral ${fmtOffset(v)} aplicado`);
        },
      }),
      btn("Ignorar", { fk: "dignore", cls: "btn-ghost btn-sm", onclick: dismissDetect }),
      warn ? alignSuggestBtn() : null,
    ),
  );
}

function alignSuggestBtn() {
  return btn("Buscar letra oficial e sincronizar", {
    fk: "d-align",
    cls: "btn-primary btn-sm",
    iconId: "wave",
    disabled: Boolean(ed.job) || dirty(),
    title: dirty() ? "Salve ou descarte as alterações antes" : null,
    onclick: (e) => {
      ed.detect = null;
      runAlign(e.currentTarget);
    },
  });
}

function dismissDetect() {
  ed.detect = null;
  keepFocus(renderSide);
  focusFk("detect");
}

function renderOffsetValue() {
  if (!ed.els.offOut || !ed.els.offOut.isConnected) return;
  ed.els.offOut.textContent = fmtOffset(ed.offset);
  if (document.activeElement !== ed.els.offRange) ed.els.offRange.value = String(ed.offset);
  ed.els.offRange.setAttribute("aria-valuetext", fmtOffset(ed.offset));
  fillRange(ed.els.offRange);
  ed.els.zero.disabled = !editable() || !ed.offset;
}

function fillRange(input) {
  const min = Number(input.min);
  const max = Number(input.max);
  input.style.setProperty("--val", `${((Number(input.value) - min) / (max - min)) * 100}%`);
}

function markPanel() {
  ed.els.pad = h(
    "button",
    {
      class: "mark-pad",
      type: "button",
      "data-fk": "pad",
      "aria-keyshortcuts": "Space Enter",
      "aria-describedby": "ed-pad-help",
      onclick: mark,
    },
    h("span", { class: "big" }, icon("target"), "MARCAR ", h("span", { class: "kbd" }, "Espaço")),
    h("span", { class: "tgt" }),
  );
  ed.els.padMeta = h("span", { class: "tnum" });
  ed.els.back = btn("Voltar uma linha", {
    fk: "back",
    cls: "btn-secondary btn-sm",
    iconId: "up",
    onclick: backLine,
  });
  const react = h("input", {
    class: "input tnum",
    id: "ed-react",
    type: "number",
    min: "0",
    max: "300",
    step: "10",
    value: String(ed.react),
  });
  react.addEventListener("change", () => {
    ed.react = Math.max(0, Math.min(300, Number(react.value) || 0));
    react.value = String(ed.react);
    lsSet(LS_REACT, ed.react);
  });
  const panel = h(
    "div",
    { class: "mark-sec" },
    h("h3", { class: "ed-sec-h" }, "Tocar e marcar"),
    ed.els.pad,
    h("div", { class: "mark-meta" }, ed.els.padMeta, ed.els.back),
    h(
      "div",
      { class: "react" },
      h("label", { for: "ed-react" }, "Compensar meu tempo de reação"),
      react,
      h("span", {}, "ms"),
    ),
    h(
      "p",
      { class: "hint flush", id: "ed-pad-help" },
      "Aperte quando a voz ",
      h("b", {}, "começar"),
      " a linha. Errou? ",
      h("span", { class: "kbd" }, "↑"),
      " volta e você marca de novo. ",
      h("span", { class: "kbd" }, "K"),
      " pausa · ",
      h("span", { class: "kbd" }, "Esc"),
      " sai do modo.",
    ),
  );
  renderPad();
  return panel;
}

function renderPad() {
  if (!ed || !ed.els.pad || !ed.els.pad.isConnected) return;
  const l = ed.lines[ed.cursor];
  const tgt = ed.els.pad.querySelector(".tgt");
  tgt.textContent = ed.countdown ? `Prepare-se… ${ed.countdown}` : l ? `“${l.text}”` : "Todas marcadas";
  ed.els.pad.disabled = !l || !editable();
  ed.els.padMeta.textContent = `Linha ${Math.min(ed.cursor + 1, ed.lines.length)} de ${ed.lines.length}`;
  ed.els.back.disabled = ed.cursor <= 0;
}

function renderLegend() {
  const k = (x) => h("span", { class: "kbd" }, x);
  const item = (keys, text) => h("span", {}, keys, ` ${text}`);
  const mark = ed.mode === "mark";
  const base = mark
    ? [
      item([k("Espaço"), "/", k("Enter")], "marcar"),
      item([k("↑"), "/", k("⌫")], "voltar uma linha"),
      item(k("K"), "pausar"),
      item([k("1"), k("2"), k("3")], "velocidade"),
      item([k("Ctrl"), "+", k("Z")], "desfazer"),
      item(k("Esc"), "sair do modo"),
    ]
    : [
      item(k("Espaço"), "tocar/pausar"),
      item([k("↑"), k("↓")], "linha"),
      item([k("←"), k("→")], "±10 ms"),
      item([k("Shift"), "+", k("←"), k("→")], "±100 ms"),
      item(k("Enter"), "ouvir linha"),
      item(k("M"), "tocar e marcar"),
      item(k("R"), "mover seguintes"),
      item([k("Ctrl"), "+", k("Z")], "desfazer"),
      item([k("Ctrl"), "+", k("S")], "salvar"),
    ];
  const full = ed.legendFull
    ? [
      item([k("Alt"), "+", k("←"), k("→")], "voltar/avançar 5 s"),
      item([k("["), k("]")], "ajuste geral ∓50 ms"),
      item([k("1"), k("2"), k("3")], "velocidade 0,5× / 0,75× / 1×"),
      item([k("Ctrl"), "+", k("Shift"), "+", k("Z")], "refazer"),
      item(k("K"), "tocar/pausar (qualquer modo)"),
      item(k("Esc"), "fechar / sair do modo"),
    ]
    : [];
  ed.els.legend.classList.toggle("full", ed.legendFull);
  replace(
    ed.els.legend,
    base,
    full,
    h("span", { class: "spacer" }),
    btn(ed.legendFull ? "Menos atalhos" : "Todos os atalhos", {
      fk: "legend",
      cls: "btn-ghost btn-sm",
      aria: ed.legendFull ? "Mostrar menos atalhos (?)" : "Mostrar todos os atalhos (?)",
      onclick: toggleLegend,
    }),
  );
}

function toggleLegend() {
  ed.legendFull = !ed.legendFull;
  keepFocus(renderLegend);
}

// ======================================================================== diálogos internos

function inner({ title, text, actions, initial }) {
  const titleId = "ed-inner-title";
  const dlg = h(
    "div",
    { class: "modal", role: "alertdialog", "aria-modal": "true", "aria-labelledby": titleId },
    h(
      "div",
      { class: "modal-head" },
      h("div", { class: "grow" }, h("h2", { id: titleId }, title), text ? h("p", {}, text) : null),
    ),
    h("div", { class: "modal-foot" }, actions),
  );
  const wrap = h("div", { class: "ed-inner" }, dlg);
  replace(ed.els.inner, wrap);
  const f = initial ? dlg.querySelector(initial) : dlg.querySelector("button");
  if (f) f.focus();
  return dlg;
}

function closeInner(focus = "list") {
  if (!ed) return;
  replace(ed.els.inner);
  focusFk(focus);
}

function innerOpen() {
  return Boolean(ed && ed.els.inner.firstChild);
}

function confirmLeave() {
  const n = M.changeCount(ed.lines, ed.offset, ed.savedOffset);
  inner({
    title: "Sair sem salvar?",
    text: `Você tem ${n} ${n === 1 ? "alteração" : "alterações"} nesta música.`,
    actions: [
      h("button", {
        class: "btn btn-ghost",
        type: "button",
        "data-init": "1",
        onclick: () => closeInner("close"),
      }, "Continuar editando"),
      h(
        "button",
        { class: "btn btn-danger-ghost", type: "button", onclick: () => closeEditor() },
        "Descartar",
      ),
      h(
        "button",
        {
          class: "btn btn-primary",
          type: "button",
          onclick: () => {
            replace(ed.els.inner);
            save({ closeAfter: true });
          },
        },
        icon("check", "icon-sm"),
        "Salvar e sair",
      ),
    ],
    initial: "[data-init]",
  });
}

function confirmDiscard() {
  const n = M.changeCount(ed.lines, ed.offset, ed.savedOffset);
  if (!n) return;
  inner({
    title: "Descartar alterações?",
    text: `As ${n === 1 ? "1 alteração" : `${n} alterações`} desta sessão serão perdidas.`,
    actions: [
      h("button", {
        class: "btn btn-ghost",
        type: "button",
        "data-init": "1",
        onclick: () => closeInner("discard"),
      }, "Continuar editando"),
      h("button", {
        class: "btn btn-danger",
        type: "button",
        onclick: () => {
          replace(ed.els.inner);
          discardChanges();
          ed.els.list.focus();
        },
      }, "Descartar"),
    ],
    initial: "[data-init]",
  });
}

function pasteInner() {
  const area = h("textarea", {
    class: "textarea",
    id: "ed-paste2",
    spellcheck: "false",
    "aria-describedby": "ed-paste2-hint",
    placeholder: "Primeira linha da música\nSegunda linha…",
  });
  const hint = h("span", { class: "hint", id: "ed-paste2-hint" }, pasteHint({}));
  const ok = h(
    "button",
    {
      class: "btn btn-primary",
      type: "button",
      disabled: true,
      onclick: async () => {
        ok.disabled = true;
        try {
          const r = await submitPastedLyrics(ed.songId, area.value);
          if (!ed) return;
          replace(ed.els.inner);
          if (r.job) ed.job = activeLyricsJob(ed.songId) || r.job;
          keepFocus(renderAll);
          ed.els.list.focus();
        } catch (err) {
          ok.disabled = false;
          hint.classList.add("err");
          hint.textContent = err.message;
        }
      },
    },
    icon("check", "icon-sm"),
    "Usar esta letra",
  );
  area.addEventListener("input", () => {
    const v = analyzePasted(area.value);
    hint.classList.toggle("err", Boolean(v.error));
    hint.textContent = pasteHint(v);
    ok.disabled = !v.ok;
  });
  const dlg = inner({
    title: "Colar letra oficial",
    text: "O texto colado substitui o extraído da voz; os tempos vêm da voz.",
    actions: [
      h("button", {
        class: "btn btn-ghost",
        type: "button",
        onclick: () => closeInner("tip-paste"),
      }, "Cancelar"),
      ok,
    ],
  });
  dlg.querySelector(".modal-head").after(
    h(
      "div",
      { class: "modal-body" },
      h("div", { class: "field" }, h("label", { for: "ed-paste2" }, "Letra oficial"), area, hint),
    ),
  );
  area.focus();
}

// ======================================================================== teclado

function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable) return true;
  return tag === "INPUT" && el.type !== "radio" && el.type !== "checkbox";
}

function trapTab(e, root) {
  const f = [
    ...root.querySelectorAll(
      "button, input, textarea, select, canvas[tabindex], [tabindex]:not([tabindex='-1'])",
    ),
  ]
    .filter((x) => !x.disabled && x.offsetParent !== null && x.tabIndex >= 0);
  if (!f.length) return;
  const a = f[0];
  const z = f[f.length - 1];
  if (e.shiftKey && document.activeElement === a) {
    e.preventDefault();
    z.focus();
  } else if (!e.shiftKey && document.activeElement === z) {
    e.preventDefault();
    a.focus();
  }
}

function onKey(e) {
  if (!ed) return;
  const t = e.target;
  if (innerOpen()) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeInner("close");
    } else if (e.key === "Tab") trapTab(e, ed.els.inner);
    return;
  }
  if (e.key === "Tab") {
    trapTab(e, ed.els.box);
    return;
  }
  const ctrl = e.ctrlKey || e.metaKey;
  const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (ctrl && key === "s") {
    e.preventDefault();
    save();
    return;
  }
  if (isTyping(t)) {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      t.blur();
      ed.els.list.focus();
    }
    return;
  }
  if (ctrl && key === "z") {
    e.preventDefault();
    if (e.shiftKey) doRedo();
    else doUndo();
    return;
  }
  if (ctrl && key === "y") {
    e.preventDefault();
    doRedo();
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    if (ed.mode === "mark") {
      pause();
      setMode("adjust");
    } else tryClose();
    return;
  }
  if (ctrl) return;
  const onButton = t.tagName === "BUTTON" && !t.classList.contains("mark-pad");
  const onRange = t.tagName === "INPUT" && t.type === "range";
  const mark = ed.mode === "mark" && ed.state === "ready";

  if (e.altKey) {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      seekAudio(clock() + (e.key === "ArrowRight" ? 5000 : -5000));
    }
    return;
  }
  if (e.key === " " || e.key === "Enter") {
    if (mark && !(onButton && e.key === "Enter" && t.dataset.fk !== "list")) {
      e.preventDefault();
      if (!e.repeat) mark();
      return;
    }
    if (onButton) return; // ativa o botão nativamente
    e.preventDefault();
    if (e.key === " ") togglePlay();
    else if (ed.state === "ready") hearLine(ed.sel);
    return;
  }
  if (onRange) return; // setas do slider continuam nativas
  switch (key) {
    case "k":
      e.preventDefault();
      togglePlay();
      return;
    case "m":
      e.preventDefault();
      setMode(ed.mode === "mark" ? "adjust" : "mark");
      return;
    case "r":
      e.preventDefault();
      ed.ripple = !ed.ripple;
      keepFocus(renderTools);
      say(ed.ripple ? "Mover as seguintes: ligado" : "Mover as seguintes: desligado");
      return;
    case "1":
    case "2":
    case "3":
      e.preventDefault();
      setRate(RATES[Number(key) - 1]);
      if (ed.mode === "mark") lsSet(LS_RATE + ".mark", ed.rate);
      return;
    case "[":
      e.preventDefault();
      setOffset(ed.offset - 50, "offset-key");
      return;
    case "]":
      e.preventDefault();
      setOffset(ed.offset + 50, "offset-key");
      return;
    case "?":
      e.preventDefault();
      toggleLegend();
      return;
  }
  if (ed.state !== "ready") return;
  if (t.closest && t.closest(".seg") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
  if (t === ed.els.canvas && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return;
  if (mark) {
    if (e.key === "ArrowUp" || e.key === "Backspace") {
      e.preventDefault();
      backLine();
    }
    return;
  }
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    if (onButton && t.closest(".seg")) return;
    e.preventDefault();
    selectLine(ed.sel + (e.key === "ArrowDown" ? 1 : -1));
    if (t !== ed.els.list) ed.els.list.focus({ preventScroll: true });
  } else if (e.key === "Home" || e.key === "End") {
    if (t !== ed.els.list) return;
    e.preventDefault();
    selectLine(e.key === "Home" ? 0 : ed.lines.length - 1);
  } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
    e.preventDefault();
    const step = e.shiftKey ? 100 : 10;
    fineShift(e.key === "ArrowRight" ? step : -step);
  }
}

// ======================================================================== laço de animação

function loop() {
  if (!ed) return;
  ed.raf = requestAnimationFrame(loop);
  const a = ed.audio;
  const now = clock();
  if (a && a.playing) {
    // mantém a voz colada no instrumental
    if (Math.abs(a.voc.currentTime - a.inst.currentTime) > 0.06) a.voc.currentTime = a.inst.currentTime;
    if (a.inst.paused && !a.inst.ended) {
      a.playing = false;
      syncPlayBtn();
    }
  }
  if (ed.els.time) {
    const txt = fmtClock(now);
    if (ed.els.time.textContent !== txt) ed.els.time.textContent = txt;
  }
  if (ed.state !== "ready" && ed.state !== "empty") return;
  const tl = now - ed.offset;
  const idx = ed.lines.length ? M.lineAt(ed.lines, tl) : -1;
  if (idx !== ed.playIdx) {
    const prev = document.getElementById(`srow-${ed.playIdx}`);
    if (prev) {
      prev.classList.remove("playing");
      replace(prev.querySelector(".st"));
    }
    const row = document.getElementById(`srow-${idx}`);
    if (row) {
      row.classList.add("playing");
      replace(row.querySelector(".st"), icon("music", "icon-sm"));
      if (a && a.playing && performance.now() - ed.userScrollAt > SCROLL_PAUSE_MS) {
        row.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
      }
    }
    ed.playIdx = idx;
  }
  renderPreview(idx, tl);
  drawWave(now);
}

function renderPreview(idx, tl) {
  const pv = ed.els.pv;
  if (!pv || !pv.isConnected) return;
  const key = `${idx}|${ed.lines[idx] ? ed.lines[idx].start : ""}|${ed.offset}`;
  if (ed.pvKey !== key) {
    ed.pvKey = key;
    const L = ed.lines[idx];
    const N = ed.lines[idx + 1] || (idx < 0 ? ed.lines[0] : null);
    replace(
      pv,
      L
        ? h(
          "p",
          { class: "cur" },
          L.text.split(/\s+/).flatMap((w, i) => [i ? " " : null, h("span", { class: "w" }, w)]),
        )
        : h("p", { class: "wait" }, ed.lines.length ? "♪ Introdução — prepare-se" : "Sem letra"),
      N && N !== L ? h("p", { class: "nxt" }, N.text) : null,
    );
  }
  if (idx < 0) return;
  const prog = M.wordProgress(ed.lines, idx, tl, durationMs());
  const spans = pv.querySelectorAll(".cur .w");
  spans.forEach((s, i) => s.style.setProperty("--p", String(prog[i] ?? 0)));
}

function drawWave(now) {
  const c = ed.els.canvas;
  if (!c || !c.isConnected || ed.waveState === "loading") return;
  const playing = ed.audio && ed.audio.playing;
  const key = `${Math.round(now / 16)}|${ed.sel}|${ed.cursor}|${c.clientWidth}`;
  if (!playing && ed.drawn === key) return;
  ed.drawn = key;
  const d = durationMs();
  const markers = [];
  ed.lines.forEach((l, i) => {
    if (l.start == null) return;
    markers.push({
      t: l.start + ed.offset,
      label: String(i + 1),
      sel: i === (ed.mode === "mark" ? ed.cursor : ed.sel),
    });
  });
  drawWindow(c, { peaks: ed.peaks, t: now, durationMs: d, spanMs: WINDOW_MS, markers });
  if (ed.els.overview) {
    drawOverview(ed.els.overview, { peaks: ed.peaks, t: now, durationMs: d, spanMs: WINDOW_MS });
  }
  c.setAttribute("aria-valuenow", String(Math.round(now)));
  c.setAttribute("aria-valuetext", fmtClock(now));
}
