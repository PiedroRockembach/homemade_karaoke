/**
 * Página do convidado `/guest` (v0.3, ADR-0003 bloco C, DESIGN §12.4).
 * Fluxo: convite no fragmento → sessão (cookie) → abas Buscar / Minha fila com SSE `role=guest`.
 * Qualquer 401/403 de sessão, `guest.ended` ou queda longa da rede leva às telas G6–G10.
 * O topo do módulo só declara coisas; o trabalho começa em `start()`, dentro de um `try`. Falhas antes do
 * boot vão para `boot-guard.js` (script clássico), que mostra o erro na tela (FIX-003).
 *
 * Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { api, ApiError } from "../shared/api.js";
import { connectEvents, getClientId } from "../shared/events.js";
import { applyEvent, createStore } from "../shared/state.js";
import { replace } from "../shared/components/dom.js";
import { myTurn, screenForEnded, sessionScreenFor } from "./logic.js";
import { captureInvite, currentGuest, renderJoin } from "./session.js";
import { initSearch } from "./search.js";
import { initMyQueue } from "./my-queue.js";
import { createToaster, statusScreen } from "./ui.js";

const OFFLINE_AFTER_MS = 30000;
const PROBE_DELAY_MS = 2000;

const $ = (id) => document.getElementById(id);

const els = {
  boot: $("boot"),
  join: $("join"),
  status: $("status"),
  app: $("app"),
  overlay: $("overlay"),
  toast: $("toast"),
  name: $("me-name"),
  reconn: $("reconn"),
  main: $("main"),
  tabSearch: $("tab-search"),
  tabQueue: $("tab-queue"),
  panelSearch: $("panel-search"),
  panelQueue: $("panel-queue"),
  badge: $("queue-badge"),
};

const store = createStore();
const toast = createToaster(els.toast);
let events = null;
let offlineTimer = 0;
let probeTimer = 0;
let libTimer = 0;
let lastTurn = null;
let ended = false;

// ------------------------------------------------------------------ tema (theme-color segue o sistema)
function syncThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  const bg = getComputedStyle(document.documentElement).getPropertyValue("--color-bg").trim();
  if (meta && bg) meta.setAttribute("content", bg);
}

function watchThemeColor() {
  syncThemeColor();
  const mq = matchMedia("(prefers-color-scheme: light)");
  if (mq.addEventListener) mq.addEventListener("change", syncThemeColor);
  else if (mq.addListener) mq.addListener(syncThemeColor); // Safari < 14
}

// ------------------------------------------------------------------ telas
function show(which) {
  for (const k of ["boot", "join", "status", "app"]) els[k].hidden = k !== which;
}

function stopLive() {
  if (events) events.close();
  events = null;
  clearTimeout(offlineTimer);
  clearTimeout(probeTimer);
  clearTimeout(libTimer);
  replace(els.overlay);
  els.app.inert = false;
  toast.hide();
}

function showStatus(kind) {
  stopLive();
  ended = true;
  const retry = () => {
    ended = false;
    boot();
  };
  replace(els.status, statusScreen(kind, retry));
  show("status");
  const title = els.status.querySelector("#state-title");
  if (title) title.focus({ preventScroll: true });
}

/** Erros de sessão vindos de qualquer chamada (401 sessão, 403 modo desligado). */
function handleSessionError(err) {
  const kind = sessionScreenFor(err);
  if (kind) showStatus(kind);
  return Boolean(kind);
}

// ------------------------------------------------------------------ abas
function goTab(name, focus = false) {
  const isQueue = name === "queue";
  els.panelSearch.hidden = isQueue;
  els.panelQueue.hidden = !isQueue;
  const [on, off] = isQueue ? [els.tabQueue, els.tabSearch] : [els.tabSearch, els.tabQueue];
  on.setAttribute("aria-current", "page");
  off.removeAttribute("aria-current");
  els.main.scrollTop = 0;
  if (focus && !isQueue) search.focusInput();
}

const ctx = {
  store,
  toast,
  overlay: els.overlay,
  inertRoot: els.app,
  goTab,
  handleSessionError,
};
let search = null;
let myQueue = null;

// ------------------------------------------------------------------ cabeçalho e barra inferior
function renderChrome(state) {
  const me = state.guest;
  els.name.textContent = (me && me.name) || "";
  const pending =
    ((state.queue && state.queue.items) || []).filter((it) => me && it.added_by === me.id).length;
  els.badge.textContent = pending ? String(pending) : "";
  els.badge.hidden = !pending;
  els.tabQueue.setAttribute(
    "aria-label",
    pending ? `Minha fila, ${pending} ${pending === 1 ? "pedido seu" : "pedidos seus"}` : "Minha fila",
  );
}

function notifyTurn(state) {
  const t = myTurn(state.queue, state.guest);
  const n = t.mineNow ? 0 : t.next ? t.next.n : null;
  if (lastTurn !== null && lastTurn > 1 && n === 1) {
    toast.show("Você é a próxima! Vá se preparando", { iconId: "mic" });
    if (navigator.vibrate) navigator.vibrate(200);
  }
  lastTurn = n;
}

store.subscribe((state, keys) => {
  if (ended || !search) return;
  renderChrome(state);
  search.render();
  myQueue.render();
  if (keys.includes("queue")) notifyTurn(state);
});

// ------------------------------------------------------------------ tempo real
function onEvent(type, data) {
  if (type === "guest.ended") {
    // O servidor fecha o stream; sem close() o backoff reconectaria e levaria 401.
    showStatus(screenForEnded(data && data.reason));
    return;
  }
  if (type === "job.updated" && data) {
    const prev = store.get().jobs[data.id];
    if (data.stage === "done" && prev && prev.stage !== "done") {
      const title = (data.song && data.song.title) || "Sua música";
      toast.show(`${title} está pronta para cantar`);
    }
  }
  if (type === "library.updated") {
    clearTimeout(libTimer);
    libTimer = setTimeout(search.loadLibrary, 500);
    return;
  }
  applyEvent(store, type, data);
}

/** Com a conexão caída, confere se a sessão ainda vale (401 → encerrada; 403 → modo desligado). */
async function probeSession() {
  try {
    await currentGuest();
  } catch (err) {
    if (!ended) handleSessionError(err);
  }
}

function onStatus(status) {
  if (ended) return;
  if (status === "open") {
    els.reconn.hidden = true;
    clearTimeout(offlineTimer);
    offlineTimer = 0;
    clearTimeout(probeTimer);
    return;
  }
  if (status === "reconnecting") {
    els.reconn.hidden = false;
    if (!offlineTimer) offlineTimer = setTimeout(() => showStatus("offline"), OFFLINE_AFTER_MS);
    clearTimeout(probeTimer);
    probeTimer = setTimeout(probeSession, PROBE_DELAY_MS);
  }
}

function startApp(guest) {
  ended = false;
  lastTurn = null;
  store.set({ guest, queue: null, jobs: {}, player: null });
  show("app");
  els.reconn.hidden = true;
  goTab("search");
  search.render(true);
  myQueue.render(true);
  search.loadLibrary();
  events = connectEvents({ role: "guest", clientId: getClientId(), onEvent, onStatus });
  // Fallback para a 1ª pintura caso o hello demore: fila atual pela API.
  api.get("/api/queue").then((q) => {
    if (!store.get().queue) store.set({ queue: q });
  }).catch((err) => handleSessionError(err));
}

// ------------------------------------------------------------------ início
async function boot() {
  show("boot");
  const token = captureInvite();
  try {
    const guest = await currentGuest();
    startApp(guest);
  } catch (err) {
    if (err.code === "guest_session_required" || err.status === 401) {
      if (!token) {
        showStatus("no_invite");
        return;
      }
      ended = false;
      renderJoin(els.join, { token, onJoined: startApp, onScreen: showStatus });
      show("join");
    } else if (sessionScreenFor(err)) {
      handleSessionError(err);
    } else if (err instanceof ApiError) {
      showStatus("offline");
    } else {
      reportFailure(err); // bug de JS, não de rede: mostra o erro em vez de "Sem conexão"
    }
  }
}

/** Falha de inicialização: a tela do boot-guard mostra a mensagem (ou, sem guarda, relança). */
function reportFailure(err) {
  const guard = globalThis.karaokeGuestGuard;
  if (guard) guard.fail(err);
  else throw err;
}

function start() {
  try {
    watchThemeColor();
    els.tabSearch.addEventListener("click", () => goTab("search"));
    els.tabQueue.addEventListener("click", () => goTab("queue"));
    search = initSearch(ctx, els.panelSearch);
    myQueue = initMyQueue(ctx, els.panelQueue);
  } catch (err) {
    reportFailure(err);
    return;
  }
  if (globalThis.karaokeGuestGuard) globalThis.karaokeGuestGuard.ok();
  boot();
}

start();
