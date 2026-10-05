/**
 * Painel Fila (US-05): reordenar (arrastar, botões ↑/↓, Alt+↑/↓), remover só se `can_remove` (toast com
 * Desfazer; v0.3: o anfitrião modera e remove também pedidos de convidados, sem Desfazer — DECISION 0006), limpar com confirmação, itens processando com etapa/% ao vivo.
 * Estado vem só de `queue.updated` (SSE); a reordenação é otimista e volta à ordem do servidor em erro.
 *
 * Dono: frontend-engineer (T-008).
 */

import { api } from "../shared/api.js";
import { emptyState } from "../shared/components/dom.js";
import { isDragging, renderQueueList, updateQueueProgress } from "../shared/components/queue-list.js";
import { modalButton, openModal, toast, toastError, withBusy } from "./ui.js";

let store = null;
let els = {};
let pendingRender = false;

export function initQueue(opts) {
  store = opts.store;
  els = {
    list: document.getElementById("queue"),
    count: document.getElementById("queue-count"),
    clear: document.getElementById("clear-queue"),
    hint: document.getElementById("queue-hint"),
  };
  els.clear.addEventListener("click", confirmClear);
  els.list.addEventListener("queue-dragend", () => {
    if (pendingRender) render();
  });
  store.subscribe((_s, keys) => {
    if (keys.includes("queue")) render();
    else if (keys.includes("jobs")) updateQueueProgress(els.list, store.get().queue, activeJobsBySong());
  });
  render();
}

/** song_id → job ativo (para o chip de preparo ao vivo). */
export function activeJobsBySong() {
  const out = {};
  for (const j of Object.values(store.get().jobs || {})) {
    if (!["done", "error", "canceled"].includes(j.stage)) out[j.song_id] = j;
  }
  return out;
}

function render(queueOverride) {
  if (isDragging()) {
    pendingRender = true;
    return;
  }
  pendingRender = false;
  const q = queueOverride || store.get().queue;
  const items = (q && q.items) || [];
  const total = items.length + (q && q.current ? 1 : 0);
  els.count.textContent = total ? `${total} ${total === 1 ? "música" : "músicas"}` : "";
  els.clear.disabled = !items.length;
  els.hint.hidden = items.length < 2;
  renderQueueList(els.list, q, {
    jobsBySong: activeJobsBySong(),
    reorderable: true,
    onMove: move,
    onRemove: remove,
    onReorder: reorder,
    empty: () =>
      emptyState({
        iconId: "list",
        compact: true,
        title: "Fila vazia",
        text: "Use Preparar e enfileirar nos resultados da busca ou Fila na biblioteca.",
      }),
  });
}

async function move(id, direction) {
  const q = store.get().queue;
  if (!q) return;
  // Otimista: move localmente já (o foco acompanha o item), servidor confirma por queue.updated.
  const items = [...q.items];
  const i = items.findIndex((it) => it.id === id);
  const j = direction === "up" ? i - 1 : i + 1;
  if (i < 0 || j < 0 || j >= items.length) return;
  [items[i], items[j]] = [items[j], items[i]];
  render({ ...q, items });
  try {
    await api.post(`/api/queue/${encodeURIComponent(id)}/move`, { direction });
  } catch (err) {
    render();
    toastError(err, "Não foi possível mover. A fila voltou à ordem do servidor.");
  }
}

async function reorder(ids) {
  const q = store.get().queue;
  if (!q) return;
  const byId = Object.fromEntries(q.items.map((it) => [it.id, it]));
  render({ ...q, items: ids.map((id) => byId[id]).filter(Boolean) });
  try {
    await api.post("/api/queue/reorder", { revision: q.revision, item_ids: ids });
  } catch (err) {
    render();
    if (err.code === "stale_queue") {
      toast("A fila mudou enquanto você arrastava. Ela voltou à ordem atual — tente de novo.", {
        level: "warning",
      });
    } else {
      toastError(err, "Não foi possível reordenar. A fila voltou à ordem do servidor.");
    }
  }
}

async function remove(item, index) {
  // Foco vai para o vizinho (por id: a lista pode ser reconstruída pelo SSE antes da resposta).
  const li = els.list.querySelector(`.q-item[data-id="${CSS.escape(item.id)}"]`);
  const hadFocus = li && li.contains(document.activeElement);
  const neighbor = li && (li.nextElementSibling || li.previousElementSibling);
  const neighborId = neighbor && neighbor.dataset.id;
  try {
    await api.del(`/api/queue/${encodeURIComponent(item.id)}`);
    if (hadFocus || !els.list.contains(document.activeElement)) {
      const target = neighborId && els.list.querySelector(`.q-item[data-id="${CSS.escape(neighborId)}"]`);
      if (target) target.focus();
    }
    const title = (item.song && item.song.title) || item.song_id;
    // Pedido de convidado: sem Desfazer (re-adicionar pelo anfitrião perderia o "pedido por").
    const fromGuest = Boolean(item.added_by_name);
    toast(
      fromGuest
        ? `“${title}” (pedido por ${item.added_by_name}) removida da fila`
        : `“${title}” removida da fila`,
      {
        undo: fromGuest ? null : async () => {
          try {
            await api.post("/api/queue", { song_id: item.song_id, singer: item.singer, position: index });
          } catch (err) {
            toastError(err);
          }
        },
      },
    );
  } catch (err) {
    if (err.code === "not_owner") {
      toast("Só quem adicionou essa música pode removê-la da fila.", { level: "warning" });
    } else toastError(err);
  }
}

function confirmClear() {
  const q = store.get().queue;
  const n = (q && q.items.length) || 0;
  if (!n) return;
  openModal({
    title: "Limpar a fila?",
    subtitle: `Remove ${n === 1 ? "a próxima música" : `as ${n} próximas músicas`}.${
      q.current ? " A que está tocando continua." : ""
    }`,
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      modalButton("Limpar fila", {
        variant: "danger",
        onclick: async (e) => {
          try {
            await withBusy(e.currentTarget, () => api.del("/api/queue"));
            close();
            toast("Fila limpa");
          } catch (err) {
            close();
            toastError(err);
          }
        },
      }),
    ],
  });
}
