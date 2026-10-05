/**
 * Ações de letra numa música pronta (DESIGN §11.5, CR-001): menu "Letra" da Biblioteca, "Buscar letra oficial e
 * sincronizar" (`POST …/lyrics/align`), "Colar letra" (LRC com tempos → 201; texto puro → 202 `{candidate, job}`
 * e alinhamento pela voz) e acompanhamento dos jobs `align`/`transcribe` pelo SSE (toast no fim com Desfazer e
 * Conferir; erro com o motivo e o que fazer).
 *
 * Dono: frontend-engineer (T-003).
 */

import { api } from "../shared/api.js";
import { h, icon } from "../shared/components/dom.js";
import { openMenu } from "./menu.js";
import { openLyrics } from "./lyrics-panel.js";
import { openSyncEditor } from "./sync-editor.js";
import { modalButton, openModal, toast, toastError, withBusy } from "./ui.js";

const TIME_TAG = /\[\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?\]/;
const META_TAG = /^\[(ar|ti|al|au|by|offset|length|re|ve|#):/i;
export const LYRICS_JOB_KINDS = ["align", "transcribe"];
const ACTIVE = ["queued", "model_download", "downloading", "separating", "lyrics", "finalizing"];

let store = null;
/** job id → {songId, prevCandidateId, title} das ações disparadas nesta aba (para o Desfazer). */
const tracked = new Map();
/** job id → última etapa vista (detecta a transição para done/error). */
const seen = new Map();

export function initLyricsActions(opts) {
  store = opts.store;
}

/** Job de letra ativo (align/transcribe) da música, ou null. */
export function activeLyricsJob(songId) {
  const jobs = (store && store.get().jobs) || {};
  return Object.values(jobs).find((j) =>
    j.song_id === songId && LYRICS_JOB_KINDS.includes(j.kind) && ACTIVE.includes(j.stage)
  ) || null;
}

/** Último job de letra da música que terminou com erro (para o ⚠ na Biblioteca), ou null. */
export function failedLyricsJob(songId) {
  const jobs = (store && store.get().jobs) || {};
  const list = Object.values(jobs).filter((j) => j.song_id === songId && LYRICS_JOB_KINDS.includes(j.kind))
    .sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
  return list.length && list[0].stage === "error" ? list[0] : null;
}

/** Rótulo curto de um job de letra para chips/banners. */
export function lyricsJobLabel(job) {
  if (!job) return "";
  if (job.stage === "queued") return "Na fila";
  const p = job.progress != null ? ` ${Math.round(job.progress * 100)}%` : "";
  return (job.kind === "transcribe" ? "Extraindo da voz" : "Sincronizando pela voz") + p;
}

function songOf(songId) {
  const lib = store && store.get().library;
  return (lib && lib.byId && lib.byId[songId]) || null;
}

function titleOf(songId, fallback) {
  const s = songOf(songId);
  return (s && s.title) || fallback || "Música";
}

function remember(job, songId, prevCandidateId) {
  if (!job || !job.id) return;
  tracked.set(job.id, { songId, prevCandidateId: prevCandidateId || null, title: titleOf(songId) });
  seen.set(job.id, job.stage);
}

/**
 * "Buscar letra oficial e sincronizar": `POST …/lyrics/align`. Sem letra oficial guardada, o backend busca no
 * LRCLIB antes de alinhar. Retorna o Job ou null.
 * @param {string} songId
 * @param {{candidateId?: string|null, language?: string|null, btn?: HTMLButtonElement|null, quiet?: boolean}} [o]
 */
export async function alignLyrics(
  songId,
  { candidateId = null, language = null, btn = null, quiet = false } = {},
) {
  const prev = currentCandidate(songId);
  const body = {};
  if (candidateId) body.candidate_id = candidateId;
  if (language) body.language = language;
  try {
    const res = await withBusy(
      btn,
      () => api.post(`/api/songs/${encodeURIComponent(songId)}/lyrics/align`, body),
    );
    const job = res && res.job;
    remember(job, songId, prev);
    if (job) mergeJob(job);
    if (!quiet) {
      toast(
        `Buscando a letra oficial de “${
          titleOf(songId)
        }” e sincronizando pela voz — acompanhe em Processamento`,
        {
          level: "info",
        },
      );
    }
    return job || null;
  } catch (err) {
    if (err.code === "song_not_ready") {
      toast("A música ainda não está pronta. Espere o preparo terminar.", { level: "warning" });
    } else if (err.code === "lyrics_text_required") {
      toast("Esse texto veio da voz. Escolha uma letra oficial ou cole a letra.", { level: "warning" });
    } else toastError(err);
    return null;
  }
}

/**
 * Envia uma letra colada (`POST …/lyrics/manual`). Com tempos → 201 Lyrics (em uso); texto puro → 202
 * `{candidate, job}` (alinhamento pela voz). Lança ApiError (o chamador mostra `invalid_lrc` no campo).
 * @returns {Promise<{kind: "lrc"|"plain", job: object|null}>}
 */
export async function submitPastedLyrics(songId, text) {
  const prev = currentCandidate(songId);
  const res = await api.post(`/api/songs/${encodeURIComponent(songId)}/lyrics/manual`, { lrc: text });
  if (res && res.candidate) {
    const job = res.job || null;
    if (job) {
      remember(job, songId, prev);
      mergeJob(job);
      toast(`Letra colada guardada — sincronizando pela voz. Ela entra em uso ao terminar.`, {
        level: "info",
      });
    } else {
      toast(
        "Letra guardada. A música ainda não está pronta: depois use “Buscar letra oficial e sincronizar”.",
        { level: "warning", duration: 8000 },
      );
    }
    return { kind: "plain", job };
  }
  toast("Letra colada salva e em uso");
  return { kind: "lrc", job: null };
}

/** Valida o texto colado: LRC (com tempos) ou texto puro (CR-001). Retorna {ok, kind?, error?, warning?}. */
export function analyzePasted(text) {
  const raw = String(text || "");
  if (!raw.trim()) return { ok: false, error: null };
  let timed = 0;
  let plain = 0;
  let firstUntimed = 0;
  raw.split(/\r?\n/).forEach((line, i) => {
    const t = line.trim();
    if (!t || META_TAG.test(t)) return;
    if (TIME_TAG.test(t)) {
      if (t.replace(new RegExp(TIME_TAG.source, "g"), "").replace(/<[^>]*>/g, "").trim()) timed++;
    } else {
      plain++;
      if (!firstUntimed) firstUntimed = i + 1;
    }
  });
  if (!timed && !plain) return { ok: false, error: "Só há marcações, sem texto de letra." };
  if (!timed) return { ok: true, kind: "plain" };
  if (plain) {
    return {
      ok: true,
      kind: "lrc",
      warning: `Linha ${firstUntimed} sem marcação de tempo — ela será ignorada. Formato: [mm:ss.xx] texto.`,
    };
  }
  return { ok: true, kind: "lrc" };
}

const PASTE_HINT =
  "Cole só o texto (uma linha por verso) — os tempos vêm da voz. Um LRC com [mm:ss.xx] também serve.";

/** Dica do campo de colar conforme o conteúdo. */
export function pasteHint(v) {
  if (v.error) return v.error;
  if (v.warning) return v.warning;
  if (v.kind === "plain") {
    return "Texto sem tempos: os tempos de cada linha e palavra vêm da voz (leva ~20 s).";
  }
  if (v.kind === "lrc") return "LRC com tempos: entra em uso como está (Manual).";
  return PASTE_HINT;
}

/** Modal "Colar letra" (texto puro ou LRC). */
export function openPasteLyrics(songId, { title = null } = {}) {
  const hint = h("span", { class: "hint", id: "pl-hint" }, PASTE_HINT);
  const area = h("textarea", {
    class: "textarea",
    id: "pl-text",
    spellcheck: "false",
    "aria-describedby": "pl-hint",
    placeholder: "Primeira linha da música\nSegunda linha…",
  });
  let saveBtn = null;
  const update = () => {
    const v = analyzePasted(area.value);
    area.setAttribute("aria-invalid", String(Boolean(v.error)));
    hint.classList.toggle("err", Boolean(v.error));
    hint.classList.toggle("warn", Boolean(v.warning));
    hint.textContent = pasteHint(v);
    if (saveBtn) saveBtn.disabled = !v.ok;
  };
  area.addEventListener("input", update);
  const modal = openModal({
    title: "Colar letra",
    subtitle: title || titleOf(songId),
    body: h("div", { class: "field" }, h("label", { for: "pl-text" }, "Letra oficial"), area, hint),
    footer: (close) => {
      saveBtn = modalButton("Usar esta letra", {
        variant: "primary",
        iconId: "check",
        disabled: true,
        onclick: async (e) => {
          try {
            await withBusy(e.currentTarget, () => submitPastedLyrics(songId, area.value));
            close();
          } catch (err) {
            if (err.code === "invalid_lrc") {
              area.setAttribute("aria-invalid", "true");
              hint.classList.add("err");
              hint.textContent = err.message;
              area.focus();
            } else toastError(err);
          }
        },
      });
      return [modalButton("Cancelar", { onclick: () => close() }), saveBtn];
    },
    initialFocus: "#pl-text",
  });
  return modal;
}

/** Menu "Letra" de uma música da Biblioteca (DESIGN §11.5). */
export function openLyricsMenu(btn, song) {
  const job = activeLyricsJob(song.id);
  const busy = job ? "Já em andamento" : null;
  openMenu(btn, {
    label: `Letra de ${song.title}`,
    items: [
      {
        label: "Editar sincronia…",
        iconId: "edit-time",
        disabled: Boolean(job),
        title: busy || "Ouvir e acertar o tempo de cada linha",
        onselect: () => openSyncEditor(song.id),
      },
      {
        label: "Buscar letra oficial e sincronizar",
        iconId: "wave",
        disabled: Boolean(job),
        title: busy || "Usa a letra oficial (busca no LRCLIB se precisar) e acerta os tempos pela voz",
        onselect: () => alignLyrics(song.id),
      },
      {
        label: "Colar letra…",
        iconId: "edit",
        disabled: Boolean(job),
        title: busy || "Cole a letra oficial, mesmo sem tempos",
        onselect: () => openPasteLyrics(song.id, { title: song.title }),
      },
      { type: "sep" },
      { label: "Candidatas e colar LRC…", iconId: "lyrics", onselect: () => openLyrics(song.id) },
    ],
  });
}

function currentCandidate(songId) {
  const s = songOf(songId);
  return (s && s.lyrics && s.lyrics.candidate_id) || null;
}

/** Coloca o job no store já (o `job.updated` pode chegar depois da resposta). */
function mergeJob(job) {
  const s = store.get();
  if (s.jobs[job.id]) return;
  store.set({ jobs: { ...s.jobs, [job.id]: job } });
}

async function undoTo(songId, candidateId) {
  try {
    await api.put(`/api/songs/${encodeURIComponent(songId)}/lyrics/selected`, { candidate_id: candidateId });
    toast("Letra anterior de volta");
  } catch (err) {
    toastError(err);
  }
}

const ERROR_TIPS = {
  align_failed:
    "A voz não casou com o texto — confira se é a letra certa, cole a letra oficial ou use Editar sincronia.",
  transcription_failed:
    "A voz está baixa demais para reconhecer — cole a letra e marque no Editar sincronia.",
};

/** Chamado a cada `job.updated`: toasts de fim/erro dos jobs de letra. */
export function onLyricsJobEvent(job) {
  if (!job || !LYRICS_JOB_KINDS.includes(job.kind)) return;
  const prev = seen.get(job.id);
  seen.set(job.id, job.stage);
  if (!prev || prev === job.stage) return;
  const info = tracked.get(job.id);
  const name = (job.song && job.song.title) || (info && info.title) || job.song_id;
  if (job.stage === "done") {
    tracked.delete(job.id);
    const actions = [{ label: "Conferir", iconId: "edit-time", onclick: () => openSyncEditor(job.song_id) }];
    const undo = info && info.prevCandidateId ? () => undoTo(job.song_id, info.prevCandidateId) : null;
    toast(
      job.kind === "transcribe"
        ? `Letra de “${name}” extraída da voz`
        : `Letra de “${name}” sincronizada pela voz`,
      { undo, actions, duration: 9000 },
    );
  } else if (job.stage === "error") {
    tracked.delete(job.id);
    const code = job.error && job.error.code;
    const msg = (job.error && job.error.message) || "A tarefa de letra falhou.";
    toast(`${name}: ${msg} ${ERROR_TIPS[code] || ""}`.trim(), { level: "error", duration: 10000 });
  }
}

/** Chip de job de letra (Biblioteca), ou null. */
export function lyricsJobChip(songId) {
  const job = activeLyricsJob(songId);
  if (!job) return null;
  return h(
    "span",
    { class: "chip chip-info", title: "Tarefa de letra em andamento — veja em Processamento" },
    icon("clock", "icon-sm"),
    lyricsJobLabel(job),
  );
}
