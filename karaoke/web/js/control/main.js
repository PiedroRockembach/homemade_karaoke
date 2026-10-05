/**
 * Entrada do Controle (/): store + SSE (`role=control`), banners globais, tema, abas, atalhos e
 * inicialização dos painéis (busca, biblioteca, processamento, fila, mini-player, letras).
 *
 * Dono: frontend-engineer (T-008).
 */

import { api } from "../shared/api.js";
import { connectEvents, getClientId } from "../shared/events.js";
import { applyEvent, createStore } from "../shared/state.js";
import { h, icon, replace } from "../shared/components/dom.js";
import { initActions } from "./actions.js";
import { initJobs, loadRecentJobs, onJobEvent } from "./jobs.js";
import { initLibrary, loadLibrary, onLibraryEvent, upsertSong } from "./library.js";
import { initLyricsActions, onLyricsJobEvent } from "./lyrics-actions.js";
import { onLyricsEvent, onSongEvent } from "./lyrics-panel.js";
import { menuOpen } from "./menu.js";
import { editCurrent, initMiniplayer } from "./miniplayer.js";
import { initSettings } from "./settings.js";
import {
  editorOpen,
  initSyncEditor,
  onEditorJobEvent,
  onEditorLyricsEvent,
  onEditorSongEvent,
  onOffsetDetected,
} from "./sync-editor.js";
import { initQueue } from "./queue.js";
import { focusSearch, initSearch } from "./search.js";
import { modalOpen, toast } from "./ui.js";

const THEME_KEY = "karaoke.theme";
const CPU_DISMISS_KEY = "karaoke.cpu_banner_dismissed";
const OFFLINE_GRACE_MS = 1500;
const HAS_SONG = ["waiting_gesture", "loading", "playing", "paused", "up_next"];

const clientId = getClientId();
const store = createStore({
  clientId,
  library: { songs: [], byId: {}, loaded: false, loading: false, error: null },
});
const ui = { stageForced: false, offline: false, offlineTimer: 0, cpuDismissed: false };

// ---------------------------------------------------------------- Palco
function openStage() {
  const w = globalThis.open("/stage", "karaoke-stage");
  if (w) w.focus();
}

// ---------------------------------------------------------------- Tema
function effectiveTheme() {
  const attr = document.documentElement.dataset.theme;
  if (attr === "light" || attr === "dark") return attr;
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
  try {
    if (theme === "light" || theme === "dark") localStorage.setItem(THEME_KEY, theme);
    else localStorage.removeItem(THEME_KEY);
  } catch {
    /* sem localStorage: vale só nesta carga */
  }
  updateThemeButton();
}

function updateThemeButton() {
  const btn = document.getElementById("btn-theme");
  const dark = effectiveTheme() === "dark";
  replace(btn, icon(dark ? "sun" : "moon"));
  const label = dark ? "Mudar para tema claro" : "Mudar para tema escuro";
  btn.setAttribute("aria-label", label);
  btn.title = label;
}

async function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    await api.patch("/api/settings", { theme: next });
  } catch {
    /* preferência fica no navegador mesmo se o servidor falhar */
  }
}

// ---------------------------------------------------------------- Abas
function selectTab(id, focus = false) {
  for (const t of document.querySelectorAll("#main-tabs .tab")) {
    const on = t.id === `tab-${id}`;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  }
  document.getElementById("pane-search").hidden = id !== "search";
  document.getElementById("pane-lib").hidden = id !== "lib";
}

function initTabs() {
  const tabs = document.getElementById("main-tabs");
  tabs.addEventListener("click", (e) => {
    const t = e.target.closest(".tab");
    if (t) selectTab(t.id.replace("tab-", ""));
  });
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const cur = tabs.querySelector('[aria-selected="true"]').id;
    selectTab(cur === "tab-search" ? "lib" : "search", true);
  });
}

// ---------------------------------------------------------------- Banners
function stageBanner(s) {
  const p = s.player;
  if (!p || !p.stage) return null;
  const { connected, audio_owner: owner } = p.stage;
  const busy = HAS_SONG.includes(p.status);
  if (!connected) {
    const playing = ["loading", "playing", "paused"].includes(p.status);
    let text;
    if (playing) {
      text = [
        h("b", {}, "Palco desconectado."),
        " A música foi pausada. Abra o Palco na TV e pressione Enter lá para continuar.",
      ];
    } else if (busy) {
      text = [
        h("b", {}, "O Palco está fechado."),
        " Tem música pronta para tocar: abra o Palco na TV e pressione Enter lá.",
      ];
    } else {
      text = [
        h("b", {}, "O Palco está fechado."),
        " Abra o Palco na TV para cantar — as músicas da fila tocam lá.",
      ];
    }
    return {
      key: `stage0|${p.status}`,
      level: "warning",
      iconId: "screen",
      text,
      action: ["Abrir Palco", openStage, "btn-primary"],
    };
  }
  if (!owner && (p.status === "waiting_gesture" || ui.stageForced)) {
    return {
      key: "stage-gesture",
      level: "warning",
      iconId: "screen",
      text: [h("b", {}, "O Palco está aberto, mas sem som."), " Pressione Enter na TV para liberar o áudio."],
      action: ["Ir para o Palco", openStage, "btn-secondary"],
    };
  }
  return null;
}

function cpuBanner(s) {
  const sys = s.system;
  if (!sys) return null;
  const warn = sys.gpu_warning || (sys.device === "cpu" ? "CUDA indisponível — usando CPU." : null);
  if (!warn) return null;
  let dismissed = false;
  try {
    dismissed = sessionStorage.getItem(CPU_DISMISS_KEY) === "1";
  } catch {
    dismissed = ui.cpuDismissed;
  }
  if (dismissed || ui.cpuDismissed) return null;
  return {
    key: `cpu|${warn}`,
    level: "info",
    iconId: "cpu",
    text: [
      h("b", {}, "GPU indisponível — usando CPU."),
      " Tudo funciona, mas preparar cada música leva vários minutos.",
    ],
    title: warn,
    action: [
      "Entendi",
      () => {
        ui.cpuDismissed = true;
        try {
          sessionStorage.setItem(CPU_DISMISS_KEY, "1");
        } catch {
          /* memória basta */
        }
        renderBanners();
      },
      "btn-ghost",
    ],
  };
}

function offlineBanner() {
  if (!ui.offline) return null;
  return {
    key: "offline",
    level: "warning",
    iconId: "wifi-off",
    role: "alert",
    text: [
      h("b", {}, "Sem conexão com o servidor do karaokê."),
      " Tentando reconectar… Se não voltar, confira se ele está rodando (make start).",
    ],
    action: null,
  };
}

function renderBanners() {
  const box = document.getElementById("banners");
  const s = store.get();
  const list = [offlineBanner(), stageBanner(s), cpuBanner(s)].filter(Boolean);
  const key = list.map((b) => b.key).join(";");
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  replace(
    box,
    list.map((b) =>
      h(
        "div",
        { class: ["banner", b.level], role: b.role || "status", title: b.title || null },
        icon(b.iconId),
        h("span", { class: "grow" }, b.text),
        b.action
          ? h(
            "button",
            { class: ["btn", b.action[2]], type: "button", onclick: b.action[1] },
            b.action[0] === "Abrir Palco" ? icon("screen") : null,
            b.action[0],
          )
          : null,
      )
    ),
  );
}

// ---------------------------------------------------------------- SSE
const TOAST_LEVELS = { info: "info", warning: "warning", error: "error" };

function onEvent(type, data) {
  if (type === "job.updated") onJobEvent(data);
  applyEvent(store, type, data);
  switch (type) {
    case "job.updated":
      onLyricsJobEvent(data);
      onEditorJobEvent(data);
      break;
    case "lyrics.offset_detected":
      onOffsetDetected(data);
      break;
    case "hello":
      loadLibrary();
      loadRecentJobs();
      if (data.settings) applyTheme(data.settings.theme);
      break;
    case "library.updated":
      onLibraryEvent(data);
      break;
    case "song.updated":
      if (store.get().library.byId[data.id]) upsertSong(data);
      onSongEvent(data);
      onEditorSongEvent(data);
      break;
    case "lyrics.updated":
      onLyricsEvent(data);
      onEditorLyricsEvent(data);
      break;
    case "settings.updated":
      if (data && data.theme) applyTheme(data.theme);
      break;
    case "toast":
      if (data && data.message) toast(data.message, { level: TOAST_LEVELS[data.level] || "info" });
      break;
    case "player.state":
      if (data && data.stage && data.stage.audio_owner) ui.stageForced = false;
      break;
  }
}

function onStatus(status) {
  if (status === "open") {
    clearTimeout(ui.offlineTimer);
    ui.offlineTimer = 0;
    if (ui.offline) {
      ui.offline = false;
      renderBanners();
    }
    return;
  }
  if (!ui.offlineTimer && !ui.offline) {
    ui.offlineTimer = setTimeout(() => {
      ui.offlineTimer = 0;
      ui.offline = true;
      renderBanners();
    }, OFFLINE_GRACE_MS);
  }
}

// ---------------------------------------------------------------- Teclado
function isTyping(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function initKeys() {
  document.addEventListener("keydown", (e) => {
    if (
      e.ctrlKey || e.metaKey || e.altKey || isTyping(document.activeElement) || modalOpen() || editorOpen() ||
      menuOpen()
    ) return;
    if (e.key === "/") {
      e.preventDefault();
      selectTab("search");
      focusSearch();
    } else if ((e.key === "e" || e.key === "E") && !e.repeat) {
      // E: editor de sincronia da música tocando (DESIGN §11.5)
      if (editCurrent()) e.preventDefault();
    }
  });
}

// ---------------------------------------------------------------- Início
function init() {
  initActions({
    store,
    clientId,
    onStageNotConnected: () => {
      ui.stageForced = true;
      renderBanners();
    },
  });
  initTabs();
  initSearch({ store, goLibrary: () => selectTab("lib", true) });
  initLibrary({
    store,
    goSearch: () => {
      selectTab("search");
      focusSearch();
    },
  });
  initJobs({ store });
  initLyricsActions({ store });
  initSyncEditor({ store });
  initSettings({ store });
  initQueue({ store });
  initMiniplayer({ store });
  initKeys();
  for (const id of ["btn-stage", "btn-stage-top"]) {
    const b = document.getElementById(id);
    if (b) b.addEventListener("click", openStage);
  }
  document.getElementById("btn-theme").addEventListener("click", toggleTheme);
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", updateThemeButton);
  updateThemeButton();
  store.subscribe((_s, keys) => {
    if (keys.includes("player") || keys.includes("system")) renderBanners();
  });
  connectEvents({ role: "control", clientId, onEvent, onStatus });
}

init();
