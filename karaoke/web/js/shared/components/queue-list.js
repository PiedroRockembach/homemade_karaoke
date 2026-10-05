/**
 * Lista da fila (contrato §8): "Tocando agora" + "Próximas" numeradas, chip de preparo para itens que
 * ainda processam, alça de arrastar, ↑/↓ sempre visíveis, remover só quando `can_remove` (DECISION 0009/0012).
 * Teclado: foco no item + Alt+↑/↓ move, Del remove. Arrastar e soltar com indicador de destino.
 *
 * v0.3: itens de convidado ganham "pedido por <nome>" (`added_by_name`, AC-16.1; `requestedBy: false` desliga).
 * Reutilizável pela futura /guest com `reorderable: false`.
 *
 * Dono: frontend-engineer (T-008).
 */

import { h, icon, preserveFocus, replace } from "./dom.js";
import { thumb } from "./song-card.js";
import { fmtPercent, jobStageLabel } from "../format.js";

let dragging = null; // id do item sendo arrastado

/** true enquanto o usuário arrasta (o chamador adia re-renderizações). */
export function isDragging() {
  return dragging !== null;
}

/** Texto do chip de preparo de um item não pronto. */
function prepLabel(item, job) {
  const j = job || item.job;
  if (item.song && item.song.status === "error") return "Falhou no preparo";
  if (!j) return "Preparando";
  if (j.stage === "queued") return "Na fila de preparo";
  const label = jobStageLabel(j.stage, j.lyrics_source) || "Preparando";
  return j.progress != null ? `${label} ${fmtPercent(j.progress)}` : label;
}

function prepChip(item, job) {
  const failed = item.song && item.song.status === "error";
  return h(
    "span",
    {
      class: ["chip", failed ? "chip-danger" : "chip-info", "q-prep"],
      dataset: { song: item.song_id },
      title: failed ? "O preparo falhou — tente de novo em Processamento" : "Será pulada até ficar pronta",
    },
    icon(failed ? "alert" : "clock"),
    h("span", { class: "q-prep-label tnum" }, prepLabel(item, job)),
  );
}

function itemLabel(item, pos, job) {
  const s = item.song || {};
  let label = `${pos ? pos + ". " : "Tocando agora: "}${s.title || item.song_id}`;
  if (s.artist) label += `, ${s.artist}`;
  if (item.singer) label += `, cantor ${item.singer}`;
  if (item.added_by_name) label += `, pedido por ${item.added_by_name}`;
  if (!item.ready) label += `, ${prepLabel(item, job)}`;
  return label;
}

/**
 * Renderiza a fila em `container`.
 * @param {HTMLElement} container
 * @param {{current: object|null, items: object[]}|null} queue
 * @param {{jobsBySong?: Record<string, object>, reorderable?: boolean,
 *          onMove?: (id: string, direction: "up"|"down") => void,
 *          onRemove?: (item: object, index: number) => void,
 *          onReorder?: (ids: string[]) => void,
 *          empty?: () => Node, requestedBy?: boolean}} opts
 */
export function renderQueueList(container, queue, opts) {
  const { jobsBySong = {}, reorderable = true, onMove, onRemove, onReorder, empty, requestedBy = true } =
    opts;
  const current = queue && queue.current;
  const items = (queue && queue.items) || [];
  preserveFocus(container, () => {
    if (!current && !items.length) {
      replace(container, empty ? empty() : null);
      return;
    }
    const nodes = [];
    if (current) {
      nodes.push(h("h3", { class: "q-section" }, "Tocando agora"));
      nodes.push(
        h(
          "div",
          { class: "q-now-wrap" },
          buildItem(current, 0, items.length, true, jobsBySong, { requestedBy }),
        ),
      );
    }
    if (items.length) {
      nodes.push(h("h3", { class: "q-section" }, "Próximas"));
      const ol = h("ol", { class: "list q-list" });
      items.forEach((it, i) =>
        ol.append(
          buildItem(it, i, items.length, false, jobsBySong, { reorderable, onMove, onRemove, requestedBy }),
        )
      );
      if (reorderable && onReorder) wireDnd(ol, items, onReorder);
      nodes.push(ol);
    }
    replace(container, nodes);
  });
}

function buildItem(item, index, total, isNow, jobsBySong, { reorderable, onMove, onRemove, requestedBy }) {
  const s = item.song || {};
  const job = jobsBySong[item.song_id];
  const pos = isNow ? 0 : index + 1;
  const canDrag = !isNow && reorderable;
  const actions = [];
  if (!isNow && reorderable && onMove) {
    actions.push(
      h(
        "button",
        {
          class: "btn btn-ghost btn-icon",
          type: "button",
          "aria-label": `Subir ${s.title || ""}`.trim(),
          title: "Subir (Alt+↑)",
          disabled: index === 0,
          dataset: { focusKey: `q:${item.id}:up` },
          onclick: () => onMove(item.id, "up"),
        },
        icon("up", "icon-sm"),
      ),
      h(
        "button",
        {
          class: "btn btn-ghost btn-icon",
          type: "button",
          "aria-label": `Descer ${s.title || ""}`.trim(),
          title: "Descer (Alt+↓)",
          disabled: index === total - 1,
          dataset: { focusKey: `q:${item.id}:down` },
          onclick: () => onMove(item.id, "down"),
        },
        icon("down", "icon-sm"),
      ),
    );
  }
  if (!isNow && item.can_remove && onRemove) {
    actions.push(
      h(
        "button",
        {
          class: "btn btn-ghost btn-icon",
          type: "button",
          "aria-label": `Remover ${s.title || ""} da fila`,
          title: "Remover (Del)",
          dataset: { focusKey: `q:${item.id}:rm` },
          onclick: () => onRemove(item, index),
        },
        icon("x", "icon-sm"),
      ),
    );
  }
  const sub = [];
  if (item.singer) sub.push(icon("user", "icon-sm"), h("span", { class: "q-singer" }, item.singer));
  else if (s.artist) sub.push(h("span", {}, s.artist));
  if (!item.ready) sub.push(prepChip(item, job));
  const by = requestedBy && item.added_by_name
    ? h(
      "div",
      { class: "q-by", title: `Pedido pelo celular por ${item.added_by_name}` },
      icon("phone", "icon-sm"),
      h("span", {}, `pedido por ${item.added_by_name}`),
    )
    : null;

  const el = h(
    isNow ? "div" : "li",
    {
      class: ["q-item", isNow && "now", !item.ready && "not-ready"],
      tabindex: "0",
      draggable: canDrag ? "true" : false,
      "aria-label": itemLabel(item, pos, job),
      dataset: { id: item.id, index: String(index), focusKey: `q:${item.id}` },
    },
    h("span", { class: "q-pos tnum", "aria-hidden": "true" }, isNow ? icon("play", "icon-sm") : String(pos)),
    h(
      "span",
      { class: "grip", title: canDrag ? "Arrastar" : null, "aria-hidden": "true" },
      canDrag ? icon("grip", "icon-sm") : null,
    ),
    thumb({ src: s.thumbnail }),
    h(
      "div",
      { class: "q-main" },
      h(
        "div",
        { class: "q-t", title: s.artist ? `${s.title} — ${s.artist}` : s.title },
        s.title || item.song_id,
      ),
      h("div", { class: "q-s" }, sub),
      by,
    ),
    h("div", { class: "q-actions" }, actions),
  );
  if (!isNow) {
    el.addEventListener("keydown", (e) => {
      if (e.target !== el) return;
      if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && reorderable && onMove) {
        e.preventDefault();
        const dir = e.key === "ArrowUp" ? "up" : "down";
        if ((dir === "up" && index > 0) || (dir === "down" && index < total - 1)) onMove(item.id, dir);
      } else if (e.key === "Delete" && item.can_remove && onRemove) {
        e.preventDefault();
        onRemove(item, index);
      }
    });
  }
  return el;
}

/** Arrastar e soltar entre os itens de `ol`. */
function wireDnd(ol, items, onReorder) {
  let target = null;
  let after = false;
  const clear = () => {
    for (const el of ol.querySelectorAll(".drop-target, .drop-after")) {
      el.classList.remove("drop-target", "drop-after");
    }
  };
  ol.addEventListener("dragstart", (e) => {
    const li = e.target.closest && e.target.closest(".q-item");
    if (!li) return;
    dragging = li.dataset.id;
    li.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", li.dataset.id);
  });
  ol.addEventListener("dragover", (e) => {
    if (!dragging) return;
    const li = e.target.closest && e.target.closest(".q-item");
    if (!li) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    const r = li.getBoundingClientRect();
    const isAfter = e.clientY > r.top + r.height / 2;
    if (li !== target || isAfter !== after) {
      clear();
      target = li;
      after = isAfter;
      li.classList.add(after ? "drop-after" : "drop-target");
    }
  });
  ol.addEventListener("dragleave", (e) => {
    if (!ol.contains(e.relatedTarget)) {
      clear();
      target = null;
    }
  });
  ol.addEventListener("drop", (e) => {
    e.preventDefault();
    const from = dragging;
    const to = target && target.dataset.id;
    clear();
    if (!from || !to) return;
    const ids = items.map((it) => it.id).filter((id) => id !== from);
    let idx = ids.indexOf(to);
    if (idx < 0) idx = ids.length;
    if (after) idx += 1;
    ids.splice(idx, 0, from);
    if (ids.join() !== items.map((it) => it.id).join()) onReorder(ids);
  });
  ol.addEventListener("dragend", () => {
    clear();
    target = null;
    for (const el of ol.querySelectorAll(".dragging")) el.classList.remove("dragging");
    const was = dragging;
    dragging = null;
    ol.dispatchEvent(new CustomEvent("queue-dragend", { bubbles: true, detail: { id: was } }));
  });
}

/** Atualiza só os chips de preparo (progresso ao vivo dos jobs), sem reconstruir a lista. */
export function updateQueueProgress(container, queue, jobsBySong) {
  if (!queue) return;
  const all = [queue.current, ...(queue.items || [])].filter(Boolean);
  for (const chip of container.querySelectorAll(".q-prep")) {
    const item = all.find((it) => it.song_id === chip.dataset.song);
    if (!item) continue;
    const label = chip.querySelector(".q-prep-label");
    const text = prepLabel(item, jobsBySong[item.song_id]);
    if (label && label.textContent !== text) label.textContent = text;
  }
}
