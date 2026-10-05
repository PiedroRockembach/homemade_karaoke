/**
 * Aba Buscar (US-01/US-02): busca no YouTube (`GET /api/search`), estados vazio/carregando/erro/sucesso,
 * busca enquanto digita (FIX-004, DECISION 0019: ~700 ms após a última tecla, ≥ 3 caracteres, abort da
 * anterior, Enter imediato; 429 mantém os resultados e tenta de novo sozinho),
 * chips Pronta/Processando/Vídeo longo/Ao vivo atualizados ao vivo pelos eventos de job e biblioteca.
 * v0.2 (T-003): botão split "Preparar e enfileirar ▾" com o modo de letra desta música (DESIGN §11.3) e chip
 * removível "Letra: <modo> ✕" quando difere do padrão.
 *
 * Dono: frontend-engineer (T-008).
 */

import { api, qs } from "../shared/api.js";
import { emptyState, h, icon, replace } from "../shared/components/dom.js";
import { resultRow, resultsSkeleton, updateResultRow } from "../shared/components/search-results.js";
import { enqueueSong, prepareAndEnqueue, prepareOnly } from "./actions.js";
import { openMenu } from "./menu.js";
import { defaultMode, LYRICS_MODES } from "./settings.js";
import { charCount, cleanQuery, createLiveSearch } from "../shared/live-search.js";

const SUGGESTIONS = ["Sucessos dos anos 80", "MPB para cantar junto", "Rock nacional"];
const SKELETON_DELAY_MS = 150;
const YT_DELAY_MS = 700; // busca depois da última tecla (DECISION 0019)
const YT_MIN_CHARS = 3;
const MAX_CHARS = 200;

const state = { query: "", mode: "empty", results: [], error: null, rateUntil: 0 };
let els = {};
let store = null;
let goLibrary = () => {};
/** youtube_id → modo de letra escolhido só para este resultado (null/ausente = padrão). */
const overrides = new Map();
let skTimer = 0;
let rateTimer = 0;
let live = null;

export function initSearch(opts) {
  store = opts.store;
  goLibrary = opts.goLibrary;
  els = {
    form: document.getElementById("search-form"),
    input: document.getElementById("q"),
    pane: document.getElementById("pane-search"),
    status: document.getElementById("search-status"),
    busy: h("span", { class: "search-busy", id: "search-busy" }),
  };
  live = createLiveSearch({
    delayMs: YT_DELAY_MS,
    minChars: YT_MIN_CHARS,
    run: (q, signal) => api.get("/api/search" + qs({ q, limit: 15 }), { signal }),
    onStart,
    onResult,
    onError,
  });
  els.form.addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch(els.input.value);
  });
  els.input.addEventListener("input", () => {
    const q = cleanQuery(els.input.value);
    if (!q) return reset();
    if (charCount(q) > MAX_CHARS) return;
    live.input(q);
    if (!live.pending) {
      // Abaixo de 3 caracteres: a busca em andamento foi cancelada.
      settle();
      if (state.mode === "loading") {
        state.mode = "empty";
        render();
      }
    }
    updateBusy();
  });
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && els.input.value) {
      e.preventDefault();
      els.input.value = "";
      reset();
    } else if (e.key === "Escape") {
      els.input.blur();
    }
  });
  store.subscribe((_s, keys) => {
    if (keys.includes("jobs") || keys.includes("library") || keys.includes("settings")) refreshStatuses();
  });
  render();
}

/** Foca a busca (atalho `/`). */
export function focusSearch() {
  els.input.focus();
  els.input.select();
}

/** Busca imediata (Enter, sugestões, "Tentar de novo"): cancela o debounce. */
export function runSearch(text) {
  const q = cleanQuery(text);
  if (!q) return reset();
  if (charCount(q) > MAX_CHARS) {
    live.cancel();
    state.mode = "error";
    state.error = { code: "invalid_request", message: "A busca pode ter no máximo 200 caracteres." };
    render();
    return;
  }
  els.input.value = q;
  live.now(q, true);
}

/** Campo vazio: volta ao estado inicial. */
function reset() {
  live.cancel();
  clearTimeout(skTimer);
  els.pane.removeAttribute("aria-busy");
  if (state.mode !== "empty") {
    state.mode = "empty";
    render();
  }
  updateBusy();
}

function onStart(q) {
  state.query = q;
  els.pane.setAttribute("aria-busy", "true");
  clearTimeout(skTimer);
  if (state.mode === "ok" && state.results.length) {
    updateBusy(); // mantém os resultados atuais com "buscando…"
    return;
  }
  // Esqueleto só se demorar (evita piscar em respostas rápidas).
  skTimer = setTimeout(() => {
    state.mode = "loading";
    render();
  }, SKELETON_DELAY_MS);
}

function settle() {
  clearTimeout(skTimer);
  els.pane.removeAttribute("aria-busy");
}

function onResult(q, res) {
  settle();
  state.query = q;
  state.results = res.results || [];
  state.mode = "ok";
  render();
  const n = state.results.length;
  if (els.status) {
    els.status.textContent = n
      ? `${n} ${n === 1 ? "resultado" : "resultados"} para “${q}”.`
      : `Nada encontrado para “${q}”.`;
  }
}

function onError(_q, err) {
  settle();
  if (err && err.code === "rate_limited") {
    const s = Number(err.details && err.details.retry_after_s);
    state.rateUntil = Date.now() + (Number.isFinite(s) && s > 0 ? Math.ceil(s) : 10) * 1000;
    live.pauseUntil(state.rateUntil);
    tickRate();
    if (state.mode === "ok" && state.results.length) return; // mantém os resultados + dica discreta
  }
  state.mode = "error";
  state.error = err;
  render();
}

function tickRate() {
  clearTimeout(rateTimer);
  updateBusy();
  if (Date.now() < state.rateUntil) rateTimer = setTimeout(tickRate, 1000);
}

/** Indicador discreto ao lado da contagem: "buscando…" ou a espera do 429. */
function updateBusy() {
  const left = Math.ceil((state.rateUntil - Date.now()) / 1000);
  let text = "";
  if (left > 0) text = `· muitas buscas seguidas — tento de novo em ${left} s`;
  else if (live && live.pending && state.mode === "ok") text = "· buscando…";
  els.busy.textContent = text;
  els.busy.classList.toggle("is-busy", text === "· buscando…");
}

/** Override efetivo do resultado (some se o padrão passou a ser o mesmo modo). */
function overrideOf(r) {
  const m = overrides.get(r.youtube_id);
  return m && m !== defaultMode() ? m : null;
}

/** Estado + chip de override (só para resultados que ainda podem ser preparados). */
function statusOf(r) {
  const st = baseStatus(r);
  const m = overrideOf(r);
  if (m && !st.live && (st.kind === "new" || st.kind === "error")) {
    st.key += `|m:${m}`;
    st.actionsKey = `m:${m}`;
    st.extraChips = () => [overrideChip(r, m)];
  }
  return st;
}

function overrideChip(r, m) {
  return h(
    "span",
    { class: "chip chip-neutral chip-override", title: "Modo de letra só para esta música" },
    icon("lyrics", "icon-sm"),
    `Letra: ${LYRICS_MODES[m].short}`,
    h(
      "button",
      {
        type: "button",
        "aria-label": "Usar o padrão",
        title: "Usar o padrão",
        onclick: () => setOverride(r, null),
      },
      icon("x"),
    ),
  );
}

function setOverride(r, mode) {
  if (mode && mode !== defaultMode()) overrides.set(r.youtube_id, mode);
  else overrides.delete(r.youtube_id);
  refreshStatuses();
  const more = document.querySelector(`#results li[data-id="${CSS.escape(r.youtube_id)}"] .split-more`);
  if (more) more.focus();
}

function openModeMenu(btn, r) {
  const def = defaultMode();
  const cur = overrideOf(r);
  openMenu(btn, {
    label: "Letra desta música",
    items: [
      { type: "header", label: "Letra desta música" },
      {
        type: "radio",
        label: `Padrão: ${LYRICS_MODES[def].name}`,
        checked: !cur,
        onselect: () => setOverride(r, null),
      },
      ...Object.entries(LYRICS_MODES).filter(([k]) => k !== def).map(([k, m]) => ({
        type: "radio",
        label: m.name,
        checked: cur === k,
        onselect: () => setOverride(r, k),
      })),
      { type: "sep" },
      {
        type: "note",
        label:
          "Vale para “Preparar e enfileirar” e “Só preparar” deste resultado. O padrão fica em Configurações.",
      },
    ],
  });
}

/** Estado de exibição de um resultado combinando busca + jobs ativos + biblioteca local. */
function baseStatus(r) {
  const s = store.get();
  const id = r.youtube_id;
  const live = Boolean(r.is_live);
  const long = Boolean(r.is_long);
  const job = Object.values(s.jobs || {}).find((j) =>
    j.song_id === id && j.kind === "process" && !["done", "error", "canceled"].includes(j.stage)
  );
  if (job) {
    const p = job.progress != null ? Math.round(job.progress * 100) : null;
    return {
      kind: "processing",
      live,
      long,
      stage: job.stage,
      progress: job.progress,
      key: `p|${job.stage}|${p}`,
    };
  }
  const lib = s.library && s.library.byId ? s.library.byId[id] : null;
  if (lib && lib.status === "ready") return { kind: "ready", live, long, key: "ready" };
  const done = Object.values(s.jobs || {}).find((j) => j.song_id === id && j.kind === "process");
  if (done && done.stage === "done") return { kind: "ready", live, long, key: "ready" };
  if (done && (done.stage === "error" || done.stage === "canceled")) {
    return { kind: "error", live, long, key: "error" };
  }
  const st = r.library && r.library.status;
  if (st === "ready") return { kind: "ready", live, long, key: "ready" };
  if (st === "processing") return { kind: "processing", live, long, stage: null, progress: null, key: "p" };
  if (st === "error") return { kind: "error", live, long, key: "error" };
  return { kind: "new", live, long, key: "new" };
}

/** Song mínima para "Adicionar à fila" a partir de um resultado. */
function songFromResult(r) {
  const lib = store.get().library;
  return (lib && lib.byId && lib.byId[r.youtube_id]) ||
    { id: r.youtube_id, title: r.title, artist: r.channel };
}

function renderActions(r, st) {
  if (st.live) {
    return [
      h(
        "span",
        { class: "hint r-why", id: `why-${r.youtube_id}` },
        "Transmissões ao vivo não podem ser preparadas. Escolha um vídeo comum.",
      ),
      h(
        "button",
        {
          class: "btn btn-primary",
          type: "button",
          disabled: true,
          "aria-describedby": `why-${r.youtube_id}`,
        },
        icon("mic"),
        "Preparar e enfileirar",
      ),
    ];
  }
  if (st.kind === "ready" || st.kind === "processing") {
    return [
      h(
        "button",
        {
          class: ["btn", st.kind === "ready" ? "btn-primary" : "btn-secondary"],
          type: "button",
          onclick: () => enqueueSong(songFromResult(r)),
        },
        icon("plus"),
        "Adicionar à fila",
      ),
    ];
  }
  const m = overrideOf(r);
  const more = h(
    "button",
    {
      class: ["btn", "btn-primary", "split-more", m && "has-override"],
      type: "button",
      "aria-label": "Opções de letra para esta música",
      title: "Como obter a letra desta música",
      "aria-haspopup": "menu",
      "aria-expanded": "false",
      onclick: (e) => openModeMenu(e.currentTarget, r),
    },
    icon("chev"),
  );
  return [
    h("button", {
      class: "btn btn-ghost",
      type: "button",
      onclick: (e) => prepareOnly(r, e.currentTarget, overrideOf(r)),
    }, st.kind === "error" ? "Tentar preparar de novo" : "Só preparar"),
    h(
      "span",
      { class: "split" },
      h(
        "button",
        {
          class: "btn btn-primary",
          type: "button",
          onclick: () => prepareAndEnqueue(r, overrideOf(r)),
          onkeydown: (e) => {
            if (e.altKey && e.key === "ArrowDown") {
              e.preventDefault();
              openModeMenu(more, r);
            }
          },
        },
        icon("mic"),
        "Preparar e enfileirar",
      ),
      more,
    ),
  ];
}

function render() {
  const pane = els.pane;
  if (state.mode === "empty") {
    replace(
      pane,
      emptyState({
        iconId: "search",
        title: "O que vamos cantar hoje?",
        text: "Busque qualquer música do YouTube. Nós tiramos a voz e buscamos a letra sincronizada.",
        extra: h(
          "p",
          { class: "hint" },
          "Pressione ",
          h("span", { class: "kbd" }, "/"),
          " para buscar de qualquer lugar.",
        ),
        actions: SUGGESTIONS.map((s) =>
          h("button", { class: "btn btn-secondary", type: "button", onclick: () => runSearch(s) }, s)
        ),
      }),
    );
    return;
  }
  if (state.mode === "loading") {
    replace(pane, resultsSkeleton(5));
    return;
  }
  if (state.mode === "error") {
    const err = state.error || {};
    const offline = err.code === "network_unavailable" || err.code === "network_error";
    const yt = err.code === "youtube_error";
    const title = offline
      ? "Sem conexão com o YouTube"
      : yt
      ? "O YouTube não respondeu como esperado"
      : "A busca falhou";
    let text = err.message || "Tente de novo em instantes.";
    if (offline) {
      text =
        "A busca precisa de internet. Verifique sua conexão e tente de novo — enquanto isso, sua Biblioteca " +
        "continua funcionando offline.";
    } else if (yt && err.details && err.details.hint) {
      text = `${err.message} ${err.details.hint}`;
    }
    replace(
      pane,
      emptyState({
        iconId: offline ? "wifi-off" : "alert",
        danger: true,
        title,
        text,
        actions: [
          h(
            "button",
            {
              class: "btn btn-primary",
              type: "button",
              onclick: () => runSearch(state.query || els.input.value),
            },
            icon("refresh"),
            "Tentar de novo",
          ),
          h("button", { class: "btn btn-secondary", type: "button", onclick: goLibrary }, "Abrir Biblioteca"),
        ],
      }),
    );
    return;
  }
  if (!state.results.length) {
    replace(
      pane,
      emptyState({
        iconId: "search",
        title: `Nada encontrado para “${state.query}”`,
        text: "Tente outras palavras: só o nome da música, ou artista + título.",
        actions: [
          h(
            "button",
            {
              class: "btn btn-secondary",
              type: "button",
              onclick: () => focusSearch(),
            },
            icon("search"),
            "Editar busca",
          ),
        ],
      }),
    );
    return;
  }
  const n = state.results.length;
  replace(
    pane,
    h(
      "p",
      { class: "hint results-count" },
      `${n} ${n === 1 ? "resultado" : "resultados"} para “${state.query}” `,
      els.busy,
    ),
    h(
      "ul",
      { class: "list", id: "results" },
      state.results.map((r) => resultRow(r, statusOf(r), renderActions)),
    ),
  );
}

function refreshStatuses() {
  if (state.mode !== "ok") return;
  const list = document.getElementById("results");
  if (!list) return;
  for (const li of list.children) {
    const r = state.results.find((x) => x.youtube_id === li.dataset.id);
    if (r) updateResultRow(li, r, statusOf(r), renderActions);
  }
}
