/**
 * Aba "Minha fila" (DESIGN §12.4 G4, US-15): tocando agora, resumo "Sua vez em N", fila completa com os
 * meus pedidos destacados, progresso do preparo dos meus pedidos e remoção com confirmação inline.
 * Tempo real pelo store (SSE `hello`, `queue.updated`, `player.state`, `job.updated`).
 *
 * Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { api } from "../shared/api.js";
import { fmtPercent, jobStageLabel } from "../shared/format.js";
import { h, icon, preserveFocus, replace } from "../shared/components/dom.js";
import { errorText, isMine, myTurn, sessionScreenFor, turnsBefore, turnText } from "./logic.js";
import { thumb } from "./ui.js";

const CONFIRM_MS = 5000;

/** Job do meu item que está preparando: o do store (mais recente) ou o resumo embutido no item. */
function jobFor(state, item) {
  const fromStore = Object.values(state.jobs || {}).find((j) => j && j.song_id === item.song_id);
  if (fromStore) {
    return { stage: fromStore.stage, progress: fromStore.progress, lyrics: fromStore.lyrics_source };
  }
  return item.job ? { stage: item.job.stage, progress: item.job.progress, lyrics: null } : null;
}

function artistOf(item) {
  return (item.song && item.song.artist) || "";
}

export function initMyQueue(ctx, root) {
  let removingId = null;
  let removeBusy = false;
  let confirmTimer = 0;
  let sig = "";
  let seen = null; // ids já mostrados: só itens novos entram com fade

  function cancelRemove(focusId) {
    clearTimeout(confirmTimer);
    removingId = null;
    render(true);
    if (focusId) {
      const b = root.querySelector(`[data-focus-key="del-${CSS.escape(focusId)}"]`);
      if (b) b.focus({ preventScroll: true });
    }
  }

  function askRemove(item) {
    clearTimeout(confirmTimer);
    removingId = item.id;
    render(true);
    const cancel = root.querySelector("[data-focus-key=rm-cancel]");
    if (cancel) cancel.focus({ preventScroll: true });
    confirmTimer = setTimeout(() => {
      if (removingId === item.id && !removeBusy) cancelRemove(null);
    }, CONFIRM_MS);
  }

  async function doRemove(item) {
    if (removeBusy) return;
    clearTimeout(confirmTimer);
    removeBusy = true;
    render(true);
    try {
      await api.del("/api/queue/" + encodeURIComponent(item.id));
      ctx.toast.show("Removida da fila");
    } catch (err) {
      if (sessionScreenFor(err)) {
        ctx.handleSessionError(err);
        return;
      }
      ctx.toast.show(errorText(err, "remove"), { iconId: "alert", tone: "warning" });
    } finally {
      removeBusy = false;
      removingId = null;
      render(true);
    }
  }

  function nowCard(state) {
    const cur = state.queue && state.queue.current;
    if (!cur) {
      return h(
        "div",
        { class: "now idle" },
        h("div", { class: "thumb" }, icon("music")),
        h(
          "div",
          { class: "txt" },
          h("div", { class: "lbl" }, "Tocando agora"),
          h("div", { class: "nt" }, "Nada tocando agora"),
        ),
      );
    }
    const mine = isMine(cur, state.guest);
    const playing = state.player && state.player.status === "playing";
    return h(
      "div",
      { class: ["now", mine && "mine"] },
      thumb(cur.song && cur.song.thumbnail),
      h(
        "div",
        { class: "txt" },
        h("div", { class: "lbl" }, "Tocando agora"),
        h("div", { class: "nt", title: cur.song && cur.song.title }, (cur.song && cur.song.title) || ""),
        h(
          "div",
          { class: "small" },
          [artistOf(cur), cur.singer].filter(Boolean).join(" · "),
          mine ? h("span", { class: "chip chip-solid" }, "Você") : null,
        ),
      ),
      playing ? h("span", { class: "eq", "aria-hidden": "true" }, h("i"), h("i"), h("i")) : null,
    );
  }

  function turnSummary(state) {
    const t = myTurn(state.queue, state.guest);
    if (t.mineNow) {
      return h(
        "div",
        { class: "turn go" },
        h("div", { class: "v" }, "É a sua vez!"),
        h("div", { class: "k" }, "Vá até o microfone — a letra está na TV."),
      );
    }
    if (!t.next) return null;
    const title = (t.next.item.song && t.next.item.song.title) || "";
    return h(
      "div",
      { class: "turn" },
      h("div", { class: "k" }, "Sua próxima: ", h("b", {}, title)),
      h("div", { class: "v" }, turnText(t.next.n)),
    );
  }

  function progressBlock(job) {
    const pct = Math.round((Number(job.progress) || 0) * 100);
    return [
      h(
        "div",
        {
          class: "bar",
          role: "progressbar",
          "aria-valuemin": "0",
          "aria-valuemax": "100",
          "aria-valuenow": String(pct),
          "aria-label": "Preparo",
        },
        h("i", { style: { width: `${pct}%` } }),
      ),
      h(
        "div",
        { class: "m" },
        h(
          "span",
          {},
          `${jobStageLabel(job.stage, job.lyrics)} · `,
          h("span", { class: "tnum" }, fmtPercent(job.progress)),
        ),
      ),
      h("div", { class: "m note" }, "Se não ficar pronta a tempo, ela espera e entra logo depois."),
    ];
  }

  function row(state, item, index) {
    const mine = isMine(item, state.guest);
    const title = (item.song && item.song.title) || "";
    const n = turnsBefore(state.queue, item.id);
    const when = turnText(n);
    if (mine && removingId === item.id) {
      return h(
        "li",
        { class: "qrow mine" },
        h(
          "div",
          { class: "confirm" },
          h("p", {}, `Remover "${title}" da fila?`),
          h(
            "button",
            {
              type: "button",
              class: ["btn", "btn-danger", removeBusy && "is-loading"],
              "aria-busy": removeBusy ? "true" : "false",
              onclick: () => doRemove(item),
            },
            removeBusy ? icon("loader") : null,
            "Remover",
          ),
          h("button", {
            type: "button",
            class: "btn btn-secondary",
            "data-focus-key": "rm-cancel",
            disabled: removeBusy,
            onclick: () => cancelRemove(item.id),
          }, "Cancelar"),
        ),
      );
    }
    const songError = item.song && item.song.status === "error";
    const job = !item.ready && !songError ? jobFor(state, item) : null;
    const meta = [h("span", {}, [artistOf(item), item.singer].filter(Boolean).join(" · "))];
    if (mine) meta.push(h("span", { class: "chip chip-solid" }, "Você"));
    if (songError) {
      meta.push(h("span", { class: "chip chip-danger" }, icon("alert"), "Não deu para preparar"));
    } else if (!item.ready && !mine) {
      meta.push(h("span", { class: "chip chip-info" }, icon("clock"), "Preparando"));
    }
    if (mine && !songError) meta.push(h("span", { class: "when" }, when));

    const label = `${title}, cantor ${item.singer || "não informado"}` +
      (mine ? `, seu pedido, ${when}` : "");
    return h(
      "li",
      { class: ["qrow", mine && "mine", seen && !seen.has(item.id) && "enter"], "aria-label": label },
      h("span", { class: "n tnum", "aria-hidden": "true" }, String(index + 1)),
      thumb(item.song && item.song.thumbnail, "thumb-sm"),
      h(
        "div",
        { class: "txt" },
        h("div", { class: "t", title }, title),
        h("div", { class: "m" }, meta),
        songError && mine ? h("div", { class: "m note" }, "Tente outra versão.") : null,
        mine && job ? h("div", { class: "prog", "data-prog": item.id }, progressBlock(job)) : null,
      ),
      item.can_remove
        ? h("button", {
          type: "button",
          class: "btn btn-ghost del",
          "aria-label": `Remover ${title} da fila`,
          "data-focus-key": `del-${item.id}`,
          onclick: () => askRemove(item),
        }, icon("trash"))
        : null,
    );
  }

  function build(state) {
    const q = state.queue;
    const items = (q && q.items) || [];
    if (!q || (!q.current && !items.length)) {
      return h(
        "div",
        { class: "empty tall" },
        h("div", { class: "ring" }, icon("list")),
        h("h2", {}, "A fila está vazia"),
        h("p", {}, "Seja o primeiro a cantar!"),
        h(
          "button",
          { type: "button", class: "btn btn-primary btn-lg", onclick: () => ctx.goTab("search", true) },
          icon("search"),
          "Buscar música",
        ),
      );
    }
    return [
      nowCard(state),
      turnSummary(state),
      h(
        "section",
        { class: "sec" },
        h("h2", { class: "sec-h" }, `Próximas · ${items.length}`),
        items.length
          ? h("ul", { class: "list", "aria-label": "Fila" }, items.map((it, i) => row(state, it, i)))
          : h("p", { class: "small pad" }, "Nenhuma música depois desta. Que tal pedir uma?"),
      ),
    ];
  }

  /** Atualiza só as barras de progresso (job.updated a até 4 Hz) sem refazer a lista. */
  function patchProgress(state) {
    for (const el of root.querySelectorAll("[data-prog]")) {
      const item = ((state.queue && state.queue.items) || []).find((it) => it.id === el.dataset.prog);
      const job = item && jobFor(state, item);
      if (job) replace(el, progressBlock(job));
    }
  }

  function render(force = false) {
    const state = ctx.store.get();
    const q = state.queue;
    const next = JSON.stringify([
      state.guest && state.guest.id,
      state.player && state.player.status,
      q && q.current && [q.current.id, q.current.singer, q.current.song && q.current.song.title],
      ((q && q.items) || []).map((it) => [
        it.id,
        it.ready,
        it.singer,
        it.can_remove,
        it.song && it.song.status,
        it.song && it.song.title,
      ]),
      removingId,
      removeBusy,
      Object.values(state.jobs || {}).map((j) => [j.id, j.stage]),
    ]);
    if (!force && next === sig) {
      patchProgress(state);
      return;
    }
    sig = next;
    preserveFocus(root, () => replace(root, build(state)));
    seen = new Set(((q && q.items) || []).map((it) => it.id));
  }

  return { render };
}
