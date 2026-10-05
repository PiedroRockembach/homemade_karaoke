/**
 * Modal "Letra — <música>" (US-03, v0.2/CR-001): candidatas com o chip de origem do backend (`label`), aviso
 * "sem letra oficial" com "Colar letra oficial", candidatas só texto (`selectable: false`) como fontes de texto;
 * rebuscar no LRCLIB; colar letra (LRC ou texto puro); "Buscar letra oficial e sincronizar"; faixa "Letra fora do
 * tempo? [Editar sincronia] · ajuste rápido −/+" (o editor substitui o antigo bloco de offset).
 *
 * Dono: frontend-engineer (T-008; v0.2: T-003).
 */

import { api } from "../shared/api.js";
import { emptyState, h, icon, replace } from "../shared/components/dom.js";
import { lyricsSourceChip, SOURCE_CHIPS } from "../shared/components/song-card.js";
import { fmtOffset, fmtTime } from "../shared/format.js";
import {
  activeLyricsJob,
  alignLyrics,
  analyzePasted,
  pasteHint,
  submitPastedLyrics,
} from "./lyrics-actions.js";
import { openSyncEditor } from "./sync-editor.js";
import { modalButton, openModal, toast, toastError, withBusy } from "./ui.js";

const DURATION_WARN_MS = 3000;
const OFFSET_MIN = -10000;
const OFFSET_MAX = 10000;
const TABS = ["cand", "paste", "voice"];

/** Estado do modal aberto (um por vez). */
let cur = null;

function sourceLabel(src) {
  return (SOURCE_CHIPS[src] && SOURCE_CHIPS[src][1]) || "sem letra";
}

function subtitleText(song) {
  const parts = [];
  if (song.artist) parts.push(song.artist);
  if (song.duration_ms) parts.push(fmtTime(song.duration_ms));
  parts.push(`origem atual: ${sourceLabel(song.lyrics && song.lyrics.source)}`);
  return parts.join(" · ");
}

/** Abre o modal de letra da música `songId`. */
export async function openLyrics(songId) {
  let song;
  try {
    song = await api.get(`/api/songs/${encodeURIComponent(songId)}`);
  } catch (err) {
    toastError(err);
    return;
  }
  const state = {
    song,
    tab: "cand",
    candidates: null,
    selectedId: null,
    chosenId: null,
    candError: null,
    offset: song.offset_ms || 0,
    offsetTimer: 0,
  };
  const enc = encodeURIComponent(songId);

  const tabBtn = (id, label, extra = null) =>
    h(
      "button",
      {
        class: "tab",
        type: "button",
        role: "tab",
        id: `lt-${id}`,
        "aria-controls": `lp-${id}`,
        "aria-selected": String(id === state.tab),
        tabindex: id === state.tab ? "0" : "-1",
        onclick: () => selectTab(id),
      },
      label,
      extra,
    );
  const candCount = h("span", { class: "chip chip-neutral", hidden: true });
  const tabs = h(
    "div",
    { class: "tabs tabs-inline", role: "tablist", "aria-label": "Opções de letra" },
    tabBtn("cand", "Candidatas", candCount),
    tabBtn("paste", "Colar letra"),
    tabBtn("voice", "Pela voz"),
  );
  tabs.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(state.tab);
    if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length];
      selectTab(next);
      document.getElementById(`lt-${next}`).focus();
    }
  });

  // --- Candidatas ---
  const candList = h("div", { class: "cand-list", "aria-live": "polite" });
  const trackInput = h("input", {
    class: "input",
    id: "lr-track",
    value: song.title || "",
    autocomplete: "off",
  });
  const artistInput = h("input", {
    class: "input",
    id: "lr-artist",
    value: song.artist || "",
    autocomplete: "off",
  });
  const researchForm = h(
    "form",
    {
      class: "research",
      onsubmit: async (e) => {
        e.preventDefault();
        const btn = e.currentTarget.querySelector("button[type=submit]");
        try {
          const res = await withBusy(btn, () =>
            api.post(`/api/songs/${enc}/lyrics/search`, {
              track_name: trackInput.value.trim() || null,
              artist_name: artistInput.value.trim() || null,
            }));
          state.candidates = res.candidates || [];
          state.candError = null;
          renderCandidates();
          toast(
            res.added
              ? `${res.added} ${
                res.added === 1 ? "nova candidata encontrada" : "novas candidatas encontradas"
              }`
              : "Nenhuma candidata nova no LRCLIB",
            { level: res.added ? "success" : "info" },
          );
        } catch (err) {
          toastError(err);
        }
      },
    },
    h("p", { class: "research-title" }, "Não achou a certa? Busque de novo no LRCLIB com outros termos:"),
    h(
      "div",
      { class: "research-fields" },
      h("div", { class: "field" }, h("label", { for: "lr-track" }, "Música"), trackInput),
      h("div", { class: "field" }, h("label", { for: "lr-artist" }, "Artista"), artistInput),
      h("button", { class: "btn btn-secondary", type: "submit" }, icon("search"), "Buscar de novo"),
    ),
  );
  const panelCand = h(
    "div",
    { id: "lp-cand", role: "tabpanel", "aria-labelledby": "lt-cand" },
    candList,
    researchForm,
  );

  // --- Colar letra (LRC ou texto puro, CR-001) ---
  const lrcHint = h("span", { class: "hint", id: "lrc-hint" }, pasteHint({}));
  const lrc = h("textarea", {
    class: "textarea",
    id: "lrc",
    spellcheck: "false",
    "aria-describedby": "lrc-hint",
    placeholder: "Primeira linha da música\nSegunda linha…\n\n(ou LRC: [00:12.40] Primeira linha…)",
  });
  lrc.addEventListener("input", () => {
    const v = analyzePasted(lrc.value);
    lrc.setAttribute("aria-invalid", String(Boolean(v.error)));
    lrcHint.classList.toggle("err", Boolean(v.error));
    lrcHint.classList.toggle("warn", Boolean(v.warning));
    lrcHint.textContent = pasteHint(v);
    updateFooter();
  });
  const panelPaste = h(
    "div",
    { id: "lp-paste", role: "tabpanel", "aria-labelledby": "lt-paste", hidden: true },
    h("div", { class: "field" }, h("label", { for: "lrc" }, "Cole a letra oficial"), lrc, lrcHint),
  );

  // --- Pela voz ---
  const lang = h(
    "select",
    { class: "input select", id: "lr-lang" },
    h("option", { value: "" }, "Detectar automaticamente"),
    h("option", { value: "pt" }, "Português"),
    h("option", { value: "en" }, "Inglês"),
    h("option", { value: "es" }, "Espanhol"),
    h("option", { value: "it" }, "Italiano"),
    h("option", { value: "fr" }, "Francês"),
  );
  const panelVoice = h(
    "div",
    { id: "lp-voice", role: "tabpanel", "aria-labelledby": "lt-voice", hidden: true },
    h(
      "div",
      { class: "voice-card" },
      h("div", { class: "vc-head" }, icon("wave"), h("b", {}, "Buscar letra oficial e sincronizar")),
      h(
        "p",
        { class: "hint" },
        "Usa a letra oficial (a selecionada, a guardada ou uma nova busca no LRCLIB) e acerta o tempo de cada linha " +
          "e palavra ouvindo a voz. Leva ~20 s na GPU. Sem letra oficial, o texto vem da voz — aí cole a letra. " +
          "A letra atual continua disponível como candidata.",
      ),
    ),
    h(
      "div",
      { class: "field narrow" },
      h("label", { for: "lr-lang" }, "Idioma da música (se precisar ouvir)"),
      lang,
    ),
  );

  // --- Faixa de sincronia (editor + ajuste rápido) ---
  const offOut = h("output", { class: "tnum", "aria-live": "polite" }, fmtOffset(state.offset));
  const setOffset = (v) => {
    state.offset = Math.max(OFFSET_MIN, Math.min(OFFSET_MAX, Math.round(v)));
    offOut.textContent = fmtOffset(state.offset);
    clearTimeout(state.offsetTimer);
    state.offsetTimer = setTimeout(async () => {
      state.offsetTimer = 0;
      try {
        state.song = await api.patch(`/api/songs/${enc}`, { offset_ms: state.offset });
      } catch (err) {
        toastError(err);
      }
    }, 250);
  };
  const syncCta = h(
    "div",
    { class: "sync-cta" },
    h(
      "div",
      { class: "grow" },
      h("b", {}, "Letra fora do tempo?"),
      h(
        "div",
        { class: "hint" },
        "Abra o editor: ouça, ajuste linha a linha ou marque no ritmo. Ou use o ajuste rápido.",
      ),
    ),
    h(
      "button",
      {
        class: "btn btn-secondary",
        type: "button",
        onclick: () => openSyncEditor(songId),
      },
      icon("edit-time"),
      "Editar sincronia",
    ),
    h(
      "div",
      { class: "offset", role: "group", "aria-label": "Ajuste rápido do offset da letra" },
      h("button", {
        type: "button",
        "aria-label": "Letra 50 ms mais cedo",
        title: "Letra mais cedo ( [ no Palco )",
        onclick: () => setOffset(state.offset - 50),
      }, "−"),
      offOut,
      h("button", {
        type: "button",
        "aria-label": "Letra 50 ms mais tarde",
        title: "Letra mais tarde ( ] no Palco )",
        onclick: () => setOffset(state.offset + 50),
      }, "+"),
    ),
  );

  const modal = openModal({
    title: `Letra — ${song.title}`,
    subtitle: subtitleText(song),
    wide: true,
    body: [tabs, panelCand, panelPaste, panelVoice, h("hr", { class: "sep" }), syncCta],
    initialFocus: "#lt-cand",
    onClose: () => {
      if (cur && cur.songId === songId) cur = null;
    },
  });
  const subtitleEl = modal.el.querySelector(".modal-head p");

  function selectTab(id) {
    state.tab = id;
    for (const t of tabs.querySelectorAll(".tab")) {
      const on = t.id === `lt-${id}`;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    }
    panelCand.hidden = id !== "cand";
    panelPaste.hidden = id !== "paste";
    panelVoice.hidden = id !== "voice";
    updateFooter();
  }

  function updateFooter() {
    const fechar = modalButton("Fechar", { onclick: () => modal.close() });
    const busy = activeLyricsJob(songId);
    let primary;
    if (state.tab === "cand") {
      const changed = state.chosenId && state.chosenId !== state.selectedId;
      primary = modalButton("Usar esta letra", {
        variant: "primary",
        iconId: "check",
        disabled: !changed,
        onclick: (e) => applyCandidate(e.currentTarget),
      });
    } else if (state.tab === "paste") {
      primary = modalButton("Salvar letra colada", {
        variant: "primary",
        iconId: "check",
        disabled: !analyzePasted(lrc.value).ok,
        onclick: (e) => savePasted(e.currentTarget),
      });
    } else {
      primary = modalButton(busy ? "Já em andamento" : "Buscar letra oficial e sincronizar", {
        variant: "primary",
        iconId: "wave",
        disabled: Boolean(busy),
        onclick: (e) => align(e.currentTarget),
      });
    }
    modal.setFooter([fechar, primary]);
  }

  async function loadCandidates() {
    replace(
      candList,
      h(
        "div",
        { class: "cand sk-cand", "aria-busy": "true" },
        h("div", { class: "sk sk-line" }),
        h("div", { class: "sk sk-line short" }),
      ),
    );
    try {
      const res = await api.get(`/api/songs/${enc}/lyrics/candidates`);
      state.candidates = res.candidates || [];
      state.selectedId = res.selected_id || null;
      if (
        !state.chosenId || !state.candidates.some((c) => c.id === state.chosenId && c.selectable !== false)
      ) {
        state.chosenId = state.selectedId;
      }
      state.candError = null;
    } catch (err) {
      state.candError = err;
    }
    renderCandidates();
  }

  function renderCandidates() {
    const list = state.candidates || [];
    const selectable = list.filter((c) => c.selectable !== false);
    const textOnly = list.filter((c) => c.selectable === false);
    candCount.hidden = !selectable.length;
    candCount.textContent = String(selectable.length);
    if (state.candError) {
      replace(
        candList,
        emptyState({
          iconId: "alert",
          compact: true,
          danger: true,
          title: "Não foi possível carregar as candidatas",
          text: state.candError.message,
          actions: [
            h(
              "button",
              { class: "btn btn-secondary", type: "button", onclick: loadCandidates },
              icon("refresh"),
              "Tentar de novo",
            ),
          ],
        }),
      );
      updateFooter();
      return;
    }
    if (!list.length) {
      replace(
        candList,
        emptyState({
          iconId: "lyrics",
          compact: true,
          title: "Nenhuma letra encontrada",
          text:
            "Busque de novo com outros termos abaixo, cole a letra ou use “Buscar letra oficial e sincronizar”.",
        }),
      );
      updateFooter();
      return;
    }
    const inUse = list.find((c) => c.id === state.selectedId);
    const noOfficial = inUse && inUse.official_text === false;
    replace(
      candList,
      noOfficial
        ? h(
          "div",
          { class: "callout warning" },
          icon("alert"),
          h(
            "div",
            {},
            h("b", {}, "Letra em uso extraída da voz (sem letra oficial)."),
            " Pode ter palavras erradas. Cole a letra oficial — mesmo sem tempos — e os tempos vêm da voz.",
            h(
              "div",
              { class: "callout-acts" },
              h(
                "button",
                {
                  class: "btn btn-secondary btn-sm",
                  type: "button",
                  onclick: () => {
                    selectTab("paste");
                    lrc.focus();
                  },
                },
                icon("edit", "icon-sm"),
                "Colar letra oficial",
              ),
            ),
          ),
        )
        : null,
      selectable.length
        ? h(
          "div",
          { role: "radiogroup", "aria-label": "Candidatas de letra" },
          selectable.map((c) => candidateRow(c)),
        )
        : null,
      textOnly.length
        ? h(
          "div",
          { class: "text-sources" },
          h("h3", { class: "set-h" }, "Fontes de texto (sem tempos)"),
          h(
            "p",
            { class: "hint flush" },
            "Não tocam sozinhas: servem de texto para sincronizar pela voz.",
          ),
          textOnly.map((c) => textOnlyRow(c)),
        )
        : null,
    );
    updateFooter();
  }

  function details(c) {
    const diff = c.duration_diff_ms;
    const differs = diff != null && Math.abs(diff) > DURATION_WARN_MS;
    const out = [];
    const who = [c.artist_name, c.track_name].filter(Boolean).join(" — ");
    if (who) out.push(who);
    if (c.duration_ms) {
      out.push(
        `Duração ${fmtTime(c.duration_ms)}` +
          (diff == null
            ? ""
            : differs
            ? ` (${Math.round(Math.abs(diff) / 1000)} s de diferença — pode dessincronizar)`
            : " (bate com o vídeo)"),
      );
    }
    out.push(
      c.timing === "word"
        ? "sincronizada por palavra"
        : c.timing === "line"
        ? "sincronizada por linha"
        : "sem tempos",
    );
    if (c.line_count) out.push(`${c.line_count} linhas`);
    if (c.align && c.align.coverage != null) {
      out.push(`${Math.round(c.align.coverage * 100)}% casado com a voz`);
    }
    return { text: out.join(" · "), differs };
  }

  function candidateRow(c) {
    const inUse = c.id === state.selectedId;
    const d = details(c);
    const chips = [lyricsSourceChip(c.source, { label: c.label })];
    if (inUse) chips.unshift(h("span", { class: "chip chip-success" }, icon("check", "icon-sm"), "Em uso"));
    if (d.differs) chips.push(h("span", { class: "chip chip-warning" }, "Duração difere"));
    const radio = h("input", {
      type: "radio",
      name: "cand",
      value: c.id,
      checked: c.id === state.chosenId,
      onchange: () => {
        state.chosenId = c.id;
        updateFooter();
      },
    });
    return h(
      "label",
      { class: "cand" },
      radio,
      h(
        "div",
        { class: "cand-main" },
        h("b", {}, c.label || sourceLabel(c.source)),
        h("div", { class: "hint" }, d.text),
        c.preview && c.preview.length ? h("pre", {}, c.preview.slice(0, 3).join("\n")) : null,
      ),
      h("span", { class: "cand-chips" }, chips),
    );
  }

  function textOnlyRow(c) {
    const d = details(c);
    const busy = activeLyricsJob(songId);
    return h(
      "div",
      { class: "cand cand-text" },
      h(
        "div",
        { class: "cand-main" },
        h("b", {}, c.label || sourceLabel(c.source)),
        h("div", { class: "hint" }, d.text),
        c.preview && c.preview.length ? h("pre", {}, c.preview.slice(0, 3).join("\n")) : null,
      ),
      h(
        "span",
        { class: "cand-chips" },
        lyricsSourceChip(c.source === "manual" ? "manual" : "online_plain", { label: c.label }),
        h(
          "button",
          {
            class: "btn btn-secondary btn-sm",
            type: "button",
            disabled: Boolean(busy),
            title: busy ? "Já em andamento" : "Acertar os tempos deste texto pela voz",
            onclick: async (e) => {
              const job = await alignLyrics(songId, { candidateId: c.id, btn: e.currentTarget });
              if (job) modal.close();
            },
          },
          icon("wave", "icon-sm"),
          "Sincronizar este texto",
        ),
      ),
    );
  }

  async function applyCandidate(btn) {
    try {
      await withBusy(
        btn,
        () => api.put(`/api/songs/${enc}/lyrics/selected`, { candidate_id: state.chosenId }),
      );
      state.selectedId = state.chosenId;
      renderCandidates();
      toast("Letra atualizada");
    } catch (err) {
      if (err.code === "candidate_not_selectable") {
        toast("Essa candidata é só texto: use “Sincronizar este texto”.", { level: "warning" });
      } else toastError(err);
    }
  }

  async function savePasted(btn) {
    try {
      const r = await withBusy(btn, () => submitPastedLyrics(songId, lrc.value));
      lrc.value = "";
      lrc.dispatchEvent(new Event("input"));
      if (r.kind === "plain" && r.job) {
        modal.close();
        return;
      }
      await loadCandidates();
      selectTab("cand");
    } catch (err) {
      if (err.code === "invalid_lrc") {
        const line = err.details && err.details.line;
        lrc.setAttribute("aria-invalid", "true");
        lrcHint.classList.add("err");
        lrcHint.textContent = (line ? `Linha ${line}: ` : "") + err.message;
        lrc.focus();
      } else toastError(err);
    }
  }

  async function align(btn) {
    const job = await alignLyrics(songId, { language: lang.value || null, btn });
    if (job) modal.close();
  }

  cur = {
    songId,
    onSong(s) {
      state.song = { ...state.song, ...s };
      subtitleEl.textContent = subtitleText(state.song);
      if (typeof s.offset_ms === "number" && !state.offsetTimer) {
        state.offset = s.offset_ms;
        offOut.textContent = fmtOffset(state.offset);
      }
    },
    onLyrics() {
      loadCandidates();
    },
  };
  updateFooter();
  loadCandidates();
}

/** Evento `song.updated` — atualiza o modal aberto, se for da mesma música. */
export function onSongEvent(song) {
  if (cur && song && cur.songId === song.id) cur.onSong(song);
}

/** Evento `lyrics.updated` — recarrega as candidatas do modal aberto. */
export function onLyricsEvent(data) {
  if (cur && data && cur.songId === data.song_id) cur.onLyrics();
}
