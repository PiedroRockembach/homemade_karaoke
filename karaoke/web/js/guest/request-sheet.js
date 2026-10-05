/**
 * Folha "Confirmar pedido" (DESIGN §12.4 G3, US-14): cantor editável (padrão = meu nome), informação de
 * quando vai ser a vez, envio e erros na própria folha.
 * - Música da Biblioteca → `POST /api/queue {song_id, singer}`.
 * - Resultado do YouTube → `POST /api/jobs {youtube_id, singer}` (o servidor prepara e enfileira).
 *
 * Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { api } from "../shared/api.js";
import { fmtTime } from "../shared/format.js";
import { h, icon, replace } from "../shared/components/dom.js";
import {
  cleanName,
  errorText,
  isFinalRequestError,
  NAME_MAX,
  nameError,
  sessionScreenFor,
  turnsBefore,
  turnsForNewRequest,
  turnText,
} from "./logic.js";
import { callout, openSheet, spinner, thumb } from "./ui.js";

function infoText(target, queue) {
  const n = turnsForNewRequest(queue);
  const when = n <= 1 ? "você deve ser a próxima" : `sua vez em ~${n} músicas`;
  if (target.kind === "new") {
    return "Vai ser preparada antes de tocar (cerca de 1 min). Se a vez chegar antes, ela espera.";
  }
  if (target.kind === "processing") return `Já está sendo preparada. Entra no fim da fila · ${when}.`;
  return `Entra no fim da fila · ${when}.`;
}

/**
 * @param {object} ctx contexto da página (store, toast, handleSessionError, goTab, host, inertRoot)
 * @param {{kind: "ready"|"processing"|"new", source: "lib"|"yt", id: string, title: string,
 *          artist: string, duration_ms: number, thumbnail: string}} target
 * @param {HTMLElement} opener botão/linha que abriu (recebe o foco de volta)
 */
export function openRequestSheet(ctx, target, opener) {
  const me = ctx.store.get().guest || {};
  const titleId = "sheet-title";
  const input = h("input", {
    id: "sheet-singer",
    class: "input",
    maxlength: String(NAME_MAX),
    autocomplete: "off",
    autocapitalize: "words",
    enterkeyhint: "send",
    value: me.name || "",
    "aria-describedby": "sheet-singer-hint",
  });
  const singerHint = h("div", { class: "hint", id: "sheet-singer-hint", "aria-live": "polite" });
  const msgBox = h("div", { class: "sheet-msg" });
  const acts = h("div", { class: "acts" });
  let busy = false;
  let sheet = null;

  const showInfo = () => {
    replace(msgBox, callout("info", "info", infoText(target, ctx.store.get().queue)));
  };

  const setActs = (mode) => {
    if (mode === "final") {
      replace(
        acts,
        h(
          "button",
          { type: "button", class: "btn btn-primary btn-lg", onclick: () => sheet.close() },
          "Entendi",
        ),
      );
      acts.querySelector("button").focus();
      return;
    }
    const sending = mode === "sending";
    replace(
      acts,
      h("button", {
        type: "button",
        class: "btn btn-ghost btn-lg",
        disabled: sending,
        onclick: () => sheet.close(),
      }, "Cancelar"),
      h(
        "button",
        { type: "submit", class: "btn btn-primary btn-lg", "aria-busy": sending ? "true" : "false" },
        sending ? [spinner(), "Pedindo…"] : "Pedir música",
      ),
    );
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    const msg = nameError(input.value, "Diga quem vai cantar.");
    if (msg) {
      input.setAttribute("aria-invalid", "true");
      singerHint.textContent = msg;
      singerHint.classList.add("err");
      input.focus();
      return;
    }
    input.setAttribute("aria-invalid", "false");
    singerHint.textContent = "";
    singerHint.classList.remove("err");
    busy = true;
    input.readOnly = true;
    setActs("sending");
    const singer = cleanName(input.value);
    try {
      let item;
      if (target.source === "lib") {
        item = await api.post("/api/queue", { song_id: target.id, singer });
      } else {
        const res = await api.post("/api/jobs", { youtube_id: target.id, singer });
        item = res && res.queue_item;
      }
      sheet.close();
      const q = ctx.store.get().queue;
      const found = item ? turnsBefore(q, item.id) : null;
      const n = found !== null ? found : turnsForNewRequest(q);
      ctx.toast.show(`Pedido! ${turnText(n).replace(/!$/, "")}`, {
        action: { label: "Ver fila", onClick: () => ctx.goTab("queue") },
      });
    } catch (err) {
      busy = false;
      input.readOnly = false;
      if (sessionScreenFor(err)) {
        sheet.close();
        ctx.handleSessionError(err);
        return;
      }
      const final = isFinalRequestError(err);
      const tone = err.code === "rate_limited" ? "warning" : "danger";
      const retry = err.status === 0
        ? [h("button", { type: "submit", class: "btn btn-secondary" }, icon("refresh"), "Tentar de novo")]
        : [];
      replace(msgBox, callout(tone, "alert", errorText(err, "request"), retry, { role: "alert" }));
      setActs(final ? "final" : "idle");
    }
  };

  const form = h(
    "form",
    { novalidate: true, onsubmit: submit },
    h(
      "div",
      { class: "sheet-head song" },
      thumb(target.thumbnail, "thumb-lg"),
      h(
        "div",
        { class: "txt" },
        h("h2", { id: titleId, tabindex: "-1", title: target.title }, target.title),
        h(
          "div",
          { class: "small" },
          [target.artist, target.duration_ms ? fmtTime(target.duration_ms) : ""].filter(Boolean).join(" · "),
        ),
      ),
    ),
    h("div", { class: "field" }, h("label", { for: "sheet-singer" }, "Quem vai cantar?"), input),
    singerHint,
    msgBox,
    acts,
  );

  input.addEventListener("input", () => {
    if (input.getAttribute("aria-invalid") === "true") {
      input.setAttribute("aria-invalid", "false");
      singerHint.textContent = "";
      singerHint.classList.remove("err");
    }
  });

  showInfo();
  setActs("idle");
  sheet = openSheet({
    host: ctx.overlay,
    inertRoot: ctx.inertRoot,
    labelledBy: titleId,
    content: form,
    opener,
  });
  // Foco no título (não abre o teclado sozinho no iOS); o campo é um toque.
  sheet.el.querySelector(`#${titleId}`).focus({ preventScroll: true });
  return sheet;
}
