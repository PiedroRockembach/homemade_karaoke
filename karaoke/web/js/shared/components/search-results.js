/**
 * Lista de resultados da busca no YouTube (contrato §4) — linhas com miniatura, título (2 linhas + title),
 * canal, chips de estado e área de ações. Reutilizável pela futura /guest: as ações são injetadas.
 *
 * Cada linha é atualizada no lugar (`updateResultRow`) quando o estado muda (job progride, fica pronta),
 * sem reconstruir a lista — não perde foco nem hover.
 *
 * Dono: frontend-engineer (T-008).
 */

import { h, icon, replace } from "./dom.js";
import { thumb } from "./song-card.js";
import { fmtPercent, fmtTime, jobStageLabel } from "../format.js";

/**
 * Estado de exibição de um resultado.
 * @typedef {{kind: "ready"|"processing"|"error"|"new", live: boolean, long: boolean,
 *            stage?: string|null, progress?: number|null, key: string,
 *            extraChips?: () => Node[], actionsKey?: string}} ResultStatus
 * `extraChips` (ex.: override do modo de letra) entra no fim dos chips; `actionsKey` força refazer as ações.
 */

/** Chips de estado de um resultado. */
function statusChips(st) {
  const chips = [];
  if (st.live) chips.push(h("span", { class: "chip chip-danger" }, "Ao vivo"));
  if (st.kind === "ready") chips.push(h("span", { class: "chip chip-success" }, icon("check"), "Pronta"));
  if (st.kind === "processing") {
    const label = st.stage === "queued"
      ? "Na fila de preparo"
      : `Processando${st.progress != null ? " " + fmtPercent(st.progress) : ""}`;
    chips.push(
      h(
        "span",
        { class: "chip chip-info", title: jobStageLabel(st.stage) || "Processando" },
        icon("clock"),
        label,
      ),
    );
  }
  if (st.kind === "error") {
    chips.push(
      h("span", { class: "chip chip-danger", title: "O último preparo falhou" }, icon("alert"), "Falhou"),
    );
  }
  if (st.long && !st.live) {
    chips.push(h("span", { class: "chip chip-warning" }, icon("alert"), "Vídeo longo"));
  }
  if (st.extraChips) chips.push(...st.extraChips());
  return chips;
}

/**
 * Cria a linha de um resultado.
 * @param {object} r resultado da busca
 * @param {ResultStatus} st
 * @param {(r: object, st: ResultStatus) => Node[]} renderActions
 */
export function resultRow(r, st, renderActions) {
  const duration = r.is_live ? "AO VIVO" : r.duration_ms ? fmtTime(r.duration_ms) : null;
  const li = h(
    "li",
    { class: "result", dataset: { id: r.youtube_id } },
    thumb({ src: r.thumbnail, duration }),
    h(
      "div",
      { class: "r-main" },
      h("div", { class: "r-title", title: r.title }, r.title),
      h(
        "div",
        { class: "r-meta" },
        r.channel ? h("span", { class: "r-channel", title: r.channel }, r.channel) : null,
        h("span", { class: "r-status" }),
      ),
    ),
    h("div", { class: "r-actions" }),
  );
  updateResultRow(li, r, st, renderActions, true);
  return li;
}

/** Atualiza chips e ações de uma linha se o estado mudou (`st.key`). */
export function updateResultRow(li, r, st, renderActions, force = false) {
  const statusEl = li.querySelector(".r-status");
  const actionsEl = li.querySelector(".r-actions");
  const kindKey = `${st.kind}|${st.live}|${st.long}|${st.actionsKey || ""}`;
  if (force || li.dataset.key !== st.key) {
    replace(statusEl, statusChips(st));
    li.dataset.key = st.key;
  }
  if (force || li.dataset.kind !== kindKey) {
    // Só troca os botões quando a categoria muda (não a cada % de progresso) — preserva o foco.
    const hadFocus = actionsEl.contains(document.activeElement);
    replace(actionsEl, renderActions(r, st));
    li.dataset.kind = kindKey;
    if (hadFocus) {
      const first = actionsEl.querySelector("button:not(:disabled)");
      if (first) first.focus({ preventScroll: true });
    }
  }
}

/** Esqueleto de carregamento (5 linhas). */
export function resultsSkeleton(n = 5) {
  return h(
    "ul",
    { class: "list", "aria-busy": "true", "aria-label": "Carregando resultados" },
    Array.from({ length: n }, () =>
      h(
        "li",
        { class: "result" },
        h("div", { class: "thumb sk" }),
        h("div", {}, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line short" })),
        h("div", { class: "sk sk-btn" }),
      )),
  );
}
