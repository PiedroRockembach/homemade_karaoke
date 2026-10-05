/**
 * Painel Processamento (US-02): jobs ao vivo via SSE (`job.updated`/`job.removed`), barra segmentada
 * Baixando · Separando voz · Buscando letra, % e ETA estimado, barra indeterminada (download de modelo),
 * erro com motivo + Tentar de novo / Descartar, cancelar job ativo. Chip GPU/CPU no cabeçalho.
 *
 * Itens são atualizados no lugar (chave por job) — progresso a 4 Hz não rouba foco.
 *
 * Dono: frontend-engineer (T-008).
 */

import { api } from "../shared/api.js";
import { emptyState, h, icon, replace } from "../shared/components/dom.js";
import { thumb } from "../shared/components/song-card.js";
import { fmtEta, fmtPercent, jobStageLabel } from "../shared/format.js";
import { jobList } from "../shared/state.js";
import { toast, toastError, withBusy } from "./ui.js";

const SEGMENTS = ["downloading", "separating", "lyrics"];
const RUNNING = ["model_download", "downloading", "separating", "lyrics", "finalizing"];
const ACTIVE = ["queued", ...RUNNING];
const ERROR_SEGMENT = {
  network_unavailable: 0,
  youtube_error: 0,
  video_unavailable: 0,
  download_failed: 0,
  ffmpeg_failed: 0,
  separation_failed: 1,
  gpu_out_of_memory: 1,
  transcription_failed: 2,
};

let store = null;
let els = {};
const lastStage = new Map(); // job id → última etapa em execução vista (para pintar o segmento do erro)
const samples = new Map(); // job id → {t0, p0} para estimar ETA
const seenStage = new Map(); // job id → etapa anterior (toasts de transição)

export function initJobs(opts) {
  store = opts.store;
  els = {
    list: document.getElementById("jobs"),
    count: document.getElementById("jobs-count"),
    device: document.getElementById("dev-chip"),
  };
  store.subscribe((_s, keys) => {
    if (keys.includes("jobs") || keys.includes("system")) render();
  });
  render();
}

/** Na reconexão, recupera também jobs com erro/cancelados recentes (o hello só traz ativos). */
export async function loadRecentJobs() {
  try {
    const res = await api.get("/api/jobs?status=recent");
    const jobs = { ...store.get().jobs };
    for (const j of res.jobs || []) {
      if (!jobs[j.id] && (j.stage === "error" || j.stage === "canceled")) jobs[j.id] = j;
      seenStage.set(j.id, j.stage);
    }
    store.set({ jobs });
  } catch {
    /* não essencial: o painel continua com os jobs ativos do hello */
  }
}

/** Toast "Pronta" quando um job termina (chamado em cada job.updated). */
export function onJobEvent(job) {
  const prev = seenStage.get(job.id);
  seenStage.set(job.id, job.stage);
  if (RUNNING.includes(job.stage)) lastStage.set(job.id, job.stage);
  if (prev && prev !== job.stage) {
    const name = (job.song && job.song.title) || job.song_id;
    if (job.stage === "done" && job.kind === "process") toast(`“${name}” está pronta`);
  }
}

function eta(job) {
  const now = performance.now();
  const p = job.progress;
  if (p == null || !RUNNING.includes(job.stage)) {
    samples.delete(job.id);
    return "";
  }
  let s = samples.get(job.id);
  if (!s || p < s.p0) {
    s = { t0: now, p0: p };
    samples.set(job.id, s);
  }
  const dt = (now - s.t0) / 1000;
  const dp = p - s.p0;
  if (dt < 4 || dp < 0.01) return "";
  return fmtEta(((1 - p) * dt) / dp);
}

function errorSegment(job) {
  const code = job.error && job.error.code;
  if (code in ERROR_SEGMENT) return ERROR_SEGMENT[code];
  const st = lastStage.get(job.id);
  if (st === "finalizing") return 2;
  const i = SEGMENTS.indexOf(st);
  return i >= 0 ? i : 0;
}

function segmentBar(job) {
  const stage = job.stage;
  const sp = job.stage_progress;
  if (stage === "model_download" || job.kind !== "process") {
    const indet = sp == null && stage !== "error";
    const span = h("span", {
      class: [stage === "error" ? "err" : stage === "done" ? "done" : "cur", indet && "indet"],
    });
    if (!indet) span.style.setProperty("--p", `${Math.round((sp ?? 0) * 100)}%`);
    return h("div", { class: "steps", "aria-hidden": "true" }, span);
  }
  const curIdx = stage === "finalizing" ? 3 : SEGMENTS.indexOf(stage);
  const errIdx = stage === "error" || stage === "canceled" ? errorSegment(job) : -1;
  return h(
    "div",
    { class: "steps", "aria-hidden": "true" },
    SEGMENTS.map((_, i) => {
      let cls = "";
      if (errIdx >= 0) cls = i < errIdx ? "done" : i === errIdx ? (stage === "error" ? "err" : "") : "";
      else if (i < curIdx) cls = "done";
      else if (i === curIdx) cls = sp == null ? "cur indet" : "cur";
      const span = h("span", { class: cls });
      if (i === curIdx && sp != null) span.style.setProperty("--p", `${Math.round(sp * 100)}%`);
      return span;
    }),
  );
}

function jobTitle(job) {
  const s = job.song || {};
  return s.artist ? `${s.title} — ${s.artist}` : s.title || job.song_id;
}

function stageLine(job) {
  if (job.stage === "queued") {
    const pos = job.queue_position;
    return h(
      "div",
      { class: "job-stage tnum" },
      h("span", {}, "Na fila de processamento"),
      h("span", {}, pos ? `${pos}º na espera` : "aguardando"),
    );
  }
  const label = kindStageLabel(job);
  let step = "";
  if (job.stage === "model_download") step = " · só no 1º uso";
  else if (job.kind === "process" && SEGMENTS.includes(job.stage)) {
    step = ` · etapa ${SEGMENTS.indexOf(job.stage) + 1} de 3`;
  }
  const right = [];
  if (job.progress != null) right.push(fmtPercent(job.progress));
  const e = eta(job);
  if (e) right.push(e);
  else if (job.stage === "model_download" && job.stage_progress == null) {
    right.push("pode levar alguns minutos");
  }
  return h(
    "div",
    { class: "job-stage tnum" },
    h("span", {}, h("b", {}, label), step),
    h("span", {}, right.join(" · ")),
  );
}

/** Rótulo da etapa por tipo de job (v0.2: align/analyze). */
function kindStageLabel(job) {
  if (job.stage === "lyrics") {
    if (job.kind === "transcribe") return "Extraindo letra da voz";
    if (job.kind === "align") return "Buscando letra oficial e sincronizando pela voz";
    if (job.kind === "analyze") return "Ouvindo a voz (ajuste automático)";
  }
  return jobStageLabel(job.stage, job.lyrics_source);
}

function jobKey(job) {
  return [
    job.stage,
    job.stage_progress == null ? "n" : Math.round(job.stage_progress * 100),
    job.progress == null ? "n" : Math.round(job.progress * 100),
    job.queue_position,
    job.error && job.error.code,
    job.lyrics_source,
    (job.song && job.song.title) || "",
  ].join("|");
}

async function retry(job, btn) {
  try {
    await withBusy(btn, () => api.post(`/api/jobs/${encodeURIComponent(job.id)}/retry`));
    toast("Tentando de novo…");
  } catch (err) {
    toastError(err);
  }
}

async function discard(job, btn, verb) {
  try {
    await withBusy(btn, () => api.del(`/api/jobs/${encodeURIComponent(job.id)}`));
    const jobs = { ...store.get().jobs };
    delete jobs[job.id];
    store.set({ jobs });
    if (verb === "cancel") {
      const name = (job.song && job.song.title) || job.song_id;
      toast(
        job.kind === "process" ? `Preparo de “${name}” cancelado` : `Tarefa de letra de “${name}” cancelada`,
      );
    }
  } catch (err) {
    if (err.code === "job_not_found") {
      const jobs = { ...store.get().jobs };
      delete jobs[job.id];
      store.set({ jobs });
    } else toastError(err);
  }
}

function buildJob(job) {
  const failed = job.stage === "error" || job.stage === "canceled";
  const title = jobTitle(job);
  const body = [h("div", { class: "t", title }, title), segmentBar(job)];
  if (failed) {
    const msg = job.stage === "canceled"
      ? (job.kind === "process" ? "Preparo cancelado." : "Tarefa cancelada.")
      : (job.error && job.error.message) ||
        (job.kind === "process" ? "O preparo falhou." : "A tarefa falhou.");
    body.push(
      h(
        "div",
        { class: "err-msg", role: job.stage === "error" ? "alert" : null },
        icon("alert", "icon-sm"),
        h("span", {}, msg),
      ),
      h(
        "div",
        { class: "job-actions" },
        h(
          "button",
          {
            class: "btn btn-primary",
            type: "button",
            dataset: { focusKey: `job:${job.id}:retry` },
            onclick: (e) => retry(job, e.currentTarget),
          },
          icon("refresh", "icon-sm"),
          "Tentar de novo",
        ),
        h("button", {
          class: "btn btn-ghost",
          type: "button",
          dataset: { focusKey: `job:${job.id}:discard` },
          onclick: (e) => discard(job, e.currentTarget, "discard"),
        }, "Descartar"),
      ),
    );
  } else {
    body.push(
      h(
        "div",
        { class: "job-row" },
        stageLine(job),
        h(
          "button",
          {
            class: "btn btn-ghost btn-icon job-cancel",
            type: "button",
            "aria-label": `${job.kind === "process" ? "Cancelar preparo" : "Cancelar tarefa"} de ${title}`,
            title: job.kind === "process" ? "Cancelar preparo" : "Cancelar tarefa",
            dataset: { focusKey: `job:${job.id}:cancel` },
            onclick: (e) => discard(job, e.currentTarget, "cancel"),
          },
          icon("x", "icon-sm"),
        ),
      ),
    );
  }
  const art = job.stage === "model_download"
    ? h("div", { class: "thumb" }, h("div", { class: "ph ph-info" }, icon("download")))
    : thumb({ src: job.song && job.song.thumbnail });
  return h(
    "div",
    {
      class: ["job", failed && "is-error"],
      dataset: { id: job.id, key: jobKey(job) },
      "aria-label": `${title}: ${failed ? "erro" : kindStageLabel(job)}`,
      role: "group",
    },
    art,
    h("div", { class: "job-main" }, body),
  );
}

function modelItem(md) {
  const indet = md.progress == null;
  const span = h("span", { class: ["cur", indet && "indet"] });
  if (!indet) span.style.setProperty("--p", `${Math.round(md.progress * 100)}%`);
  return h(
    "div",
    { class: "job", dataset: { id: "model", key: `m|${md.model_id}|${md.progress}` }, role: "group" },
    h("div", { class: "thumb" }, h("div", { class: "ph ph-info" }, icon("download"))),
    h(
      "div",
      { class: "job-main" },
      h("div", { class: "t" }, "Modelos de IA — só no primeiro uso"),
      h("div", { class: "steps", "aria-hidden": "true" }, span),
      h(
        "div",
        { class: "job-stage tnum" },
        h("span", {}, h("b", {}, "Baixando modelo de IA"), ` · ${md.model_id}`),
        h("span", {}, indet ? "pode levar alguns minutos" : fmtPercent(md.progress)),
      ),
    ),
  );
}

function render() {
  const s = store.get();
  // Chip de dispositivo
  const dev = s.system && s.system.device;
  if (dev === "cuda") {
    replace(
      els.device,
      h("span", { class: "chip chip-info", title: "Separação e Whisper na GPU (NVIDIA)" }, "GPU"),
    );
  } else if (dev === "cpu") {
    replace(
      els.device,
      h("span", { class: "chip chip-warning", title: "CUDA indisponível — processamento mais lento" }, "CPU"),
    );
  } else replace(els.device);

  const jobs = jobList(s).filter((j) => j.stage !== "done");
  const items = jobs.map((j) => ({ id: j.id, key: jobKey(j), build: () => buildJob(j) }));
  const md = s.system && s.system.model_download;
  if (md && !jobs.some((j) => j.stage === "model_download")) {
    items.unshift({ id: "model", key: `m|${md.model_id}|${md.progress}`, build: () => modelItem(md) });
  }
  const active = jobs.filter((j) => ACTIVE.includes(j.stage)).length;
  els.count.textContent = active ? String(active) : "";

  if (!items.length) {
    if (!els.list.querySelector(".empty")) {
      replace(
        els.list,
        emptyState({
          iconId: "cpu",
          compact: true,
          title: "Nada sendo preparado",
          text:
            "Ao escolher uma música, as etapas aparecem aqui: Baixando → Separando voz → Buscando letra → Pronta.",
        }),
      );
    }
    return;
  }
  reconcile(items);
}

/** Atualiza os filhos de `els.list` por chave, trocando só os itens que mudaram. */
function reconcile(items) {
  const list = els.list;
  const empty = list.querySelector(".empty");
  if (empty) empty.remove();
  const existing = new Map([...list.children].map((el) => [el.dataset.id, el]));
  let prev = null;
  for (const it of items) {
    let el = existing.get(it.id);
    existing.delete(it.id);
    if (!el || el.dataset.key !== it.key) {
      const fresh = it.build();
      const active = document.activeElement;
      const focusKey = el && el.contains(active) ? active.getAttribute("data-focus-key") : null;
      if (el) el.replaceWith(fresh);
      el = fresh;
      if (focusKey) {
        const again = el.querySelector(`[data-focus-key="${CSS.escape(focusKey)}"]`);
        if (again) again.focus({ preventScroll: true });
      }
    }
    const want = prev ? prev.nextSibling : list.firstChild;
    if (el !== want) list.insertBefore(el, want);
    prev = el;
  }
  for (const el of existing.values()) el.remove();
}
