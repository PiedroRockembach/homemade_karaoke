/**
 * Aba Biblioteca (US-04): lista de músicas prontas (`GET /api/songs`), filtro local (sem acento/caixa),
 * origem da letra (chip v0.2; job de letra em andamento no lugar do chip), ações Fila / menu Letra / Excluir
 * (excluir só se `can_remove`, com confirmação). Mantida ao vivo por `library.updated`, `song.updated` e jobs.
 *
 * Dono: frontend-engineer (T-008).
 */

import { api, qs } from "../shared/api.js";
import { emptyState, h, icon, normalize, preserveFocus, replace } from "../shared/components/dom.js";
import { fmtSize, lyricsSourceChip, songMeta, thumb } from "../shared/components/song-card.js";
import { enqueueSong } from "./actions.js";
import { failedLyricsJob, lyricsJobChip, openLyricsMenu } from "./lyrics-actions.js";
import { modalButton, openModal, toast, toastError, withBusy } from "./ui.js";

const SKELETON_DELAY_MS = 300;
let store = null;
let els = {};
let filter = "";
let goSearch = () => {};
let jobsSig = "";

export function initLibrary(opts) {
  store = opts.store;
  goSearch = opts.goSearch;
  els = { pane: document.getElementById("pane-lib"), count: document.getElementById("lib-count") };
  store.subscribe((_s, keys) => {
    if (keys.includes("library")) render();
    else if (keys.includes("jobs")) {
      const sig = lyricsJobsSignature();
      if (sig !== jobsSig) {
        jobsSig = sig;
        renderList();
      }
    }
  });
  render();
}

function setLibrary(patch) {
  const cur = store.get().library;
  const next = { ...cur, ...patch };
  if (patch.songs) next.byId = Object.fromEntries(patch.songs.map((s) => [s.id, s]));
  store.set({ library: next });
}

/** Carrega a biblioteca inteira (chamado a cada `hello`). */
export async function loadLibrary() {
  const timer = setTimeout(() => {
    if (!store.get().library.loaded) setLibrary({ loading: true });
  }, SKELETON_DELAY_MS);
  try {
    const res = await api.get("/api/songs" + qs({ status: "all", sort: "recent" }));
    setLibrary({ songs: res.songs || [], loaded: true, loading: false, error: null });
  } catch (err) {
    setLibrary({ loading: false, error: err });
    if (store.get().library.loaded) toastError(err, "Não foi possível atualizar a biblioteca.");
  } finally {
    clearTimeout(timer);
  }
}

/** Insere/atualiza uma Song (eventos). Novas entram no topo (ordem "recentes"). */
export function upsertSong(song) {
  if (!song || !song.id) return;
  const lib = store.get().library;
  const songs = [...(lib.songs || [])];
  const i = songs.findIndex((s) => s.id === song.id);
  if (i >= 0) songs[i] = { ...songs[i], ...song };
  else songs.unshift(song);
  setLibrary({ songs });
}

export function removeSong(id) {
  const lib = store.get().library;
  setLibrary({ songs: (lib.songs || []).filter((s) => s.id !== id) });
}

/** Evento `library.updated`. */
export function onLibraryEvent(data) {
  if (!data) return;
  if (data.action === "removed") removeSong(data.song_id);
  else if (data.song) upsertSong(data.song);
}

/** Assinatura dos jobs de letra (muda a cada etapa/% inteiro) — evita redesenhar a lista a 4 Hz à toa. */
function lyricsJobsSignature() {
  return Object.values(store.get().jobs || {})
    .filter((j) => j.kind === "align" || j.kind === "transcribe")
    .map((j) => `${j.id}:${j.stage}:${j.progress == null ? "" : Math.round(j.progress * 100)}`)
    .sort()
    .join(",");
}

function sourceCell(song) {
  const job = lyricsJobChip(song.id);
  if (job) return job;
  const chip = lyricsSourceChip(song.lyrics && song.lyrics.source, { short: true });
  const failed = failedLyricsJob(song.id);
  if (!failed) return chip;
  return [
    chip,
    h(
      "span",
      {
        class: "src-err",
        title: `Última tarefa de letra falhou: ${(failed.error && failed.error.message) || "erro"}`,
        role: "img",
        "aria-label": "Última tarefa de letra falhou",
      },
      icon("alert", "icon-sm"),
    ),
  ];
}

function confirmDelete(song) {
  const size = fmtSize(song.size_bytes);
  openModal({
    title: `Excluir “${song.title}”?`,
    subtitle: `Remove da biblioteca e apaga os arquivos${
      size ? ` (≈ ${size})` : ""
    }. Ela também sai da fila.`,
    body: h("p", { class: "hint flush" }, "Para cantar de novo será preciso baixar e processar outra vez."),
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      modalButton("Excluir música", {
        variant: "danger",
        iconId: "trash",
        onclick: async (e) => {
          try {
            await withBusy(e.currentTarget, () => api.del(`/api/songs/${encodeURIComponent(song.id)}`));
            close();
            removeSong(song.id);
            toast(`“${song.title}” excluída da biblioteca`);
          } catch (err) {
            close();
            if (err.code === "song_in_use") {
              toast("Essa música está tocando agora. Pule para a próxima antes de excluir.", {
                level: "warning",
              });
            } else if (err.code === "not_owner") {
              toast("Só quem adicionou essa música pode excluí-la.", { level: "warning" });
            } else {
              toastError(err);
            }
          }
        },
      }),
    ],
  });
}

function row(song) {
  const actions = [
    h(
      "button",
      {
        class: "btn btn-secondary",
        type: "button",
        "aria-label": `Adicionar ${song.title} à fila`,
        title: "Adicionar à fila",
        dataset: { focusKey: `lib:${song.id}:q` },
        onclick: () => enqueueSong(song),
      },
      icon("plus"),
      h("span", { class: "txt" }, "Fila"),
    ),
    h(
      "button",
      {
        class: "btn btn-ghost lyr-menu-btn",
        type: "button",
        "aria-label": `Gerir letra de ${song.title}`,
        title: "Letra e sincronia",
        "aria-haspopup": "menu",
        "aria-expanded": "false",
        dataset: { focusKey: `lib:${song.id}:l` },
        onclick: (e) => openLyricsMenu(e.currentTarget, song),
      },
      icon("lyrics"),
      icon("chev", "icon-sm"),
    ),
  ];
  if (song.can_remove) {
    actions.push(
      h(
        "button",
        {
          class: "btn btn-danger-ghost btn-icon",
          type: "button",
          "aria-label": `Excluir ${song.title}`,
          title: "Excluir",
          dataset: { focusKey: `lib:${song.id}:d` },
          onclick: () => confirmDelete(song),
        },
        icon("trash"),
      ),
    );
  }
  return h(
    "li",
    { class: "lib-row", dataset: { id: song.id } },
    thumb({ src: song.thumbnail }),
    h(
      "div",
      { class: "lib-main" },
      h(
        "div",
        { class: "q-t", title: song.artist ? `${song.title} — ${song.artist}` : song.title },
        song.title,
      ),
      h("div", { class: "q-s" }, songMeta(song)),
    ),
    h("div", { class: "lib-src" }, sourceCell(song)),
    h("div", { class: "r-actions" }, actions),
  );
}

function filterInput() {
  const input = h("input", {
    class: "input",
    id: "libq",
    type: "search",
    placeholder: "Filtrar por título ou artista",
    autocomplete: "off",
    value: filter,
  });
  input.addEventListener("input", () => {
    filter = input.value;
    renderList();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && input.value) {
      e.preventDefault();
      e.stopPropagation();
      input.value = "";
      filter = "";
      renderList();
    }
  });
  return h(
    "div",
    { class: "lib-toolbar" },
    h(
      "div",
      { class: "search" },
      icon("search"),
      h("label", { class: "sr-only", for: "libq" }, "Filtrar biblioteca"),
      input,
    ),
  );
}

function readySongs() {
  return (store.get().library.songs || []).filter((s) => s.status === "ready");
}

function render() {
  const lib = store.get().library;
  const songs = readySongs();
  els.count.textContent = String(songs.length);
  els.count.hidden = !lib.loaded;
  const pane = els.pane;
  if (!lib.loaded) {
    if (lib.error) {
      replace(
        pane,
        emptyState({
          iconId: "alert",
          danger: true,
          title: "Não foi possível abrir a biblioteca",
          text: lib.error.message,
          actions: [
            h(
              "button",
              { class: "btn btn-primary", type: "button", onclick: loadLibrary },
              icon("refresh"),
              "Tentar de novo",
            ),
          ],
        }),
      );
    } else if (lib.loading) {
      replace(
        pane,
        h(
          "ul",
          { class: "list", "aria-busy": "true", "aria-label": "Carregando biblioteca" },
          Array.from({ length: 4 }, () =>
            h(
              "li",
              { class: "lib-row" },
              h("div", { class: "thumb sk" }),
              h("div", {}, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line short" })),
            )),
        ),
      );
    } else {
      replace(pane);
    }
    return;
  }
  if (!songs.length) {
    replace(
      pane,
      emptyState({
        iconId: "music",
        title: "Sua biblioteca está vazia",
        text: "Toda música que você preparar fica guardada aqui e funciona offline, sem processar de novo.",
        actions: [
          h(
            "button",
            { class: "btn btn-primary", type: "button", onclick: goSearch },
            icon("search"),
            "Buscar a primeira música",
          ),
        ],
      }),
    );
    return;
  }
  if (!pane.querySelector("#libq")) replace(pane, filterInput(), h("div", { id: "lib-list" }));
  renderList();
}

function renderList() {
  const box = document.getElementById("lib-list");
  if (!box) return;
  const f = normalize(filter.trim());
  const songs = readySongs().filter((s) =>
    !f || normalize(`${s.title} ${s.artist || ""} ${s.original_title || ""}`).includes(f)
  );
  preserveFocus(box, () => {
    if (!songs.length) {
      replace(
        box,
        emptyState({
          iconId: "search",
          compact: true,
          title: `Nada encontrado para “${filter.trim()}”`,
          text: "Confira a grafia ou busque no YouTube.",
          actions: [
            h("button", {
              class: "btn btn-secondary",
              type: "button",
              onclick: () => {
                filter = "";
                const input = document.getElementById("libq");
                input.value = "";
                input.focus();
                renderList();
              },
            }, "Limpar filtro"),
          ],
        }),
      );
      return;
    }
    replace(box, h("ul", { class: "list", "aria-label": "Músicas da biblioteca" }, songs.map(row)));
  });
}
