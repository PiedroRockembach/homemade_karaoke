/**
 * Peças de interface da página /guest: toast, capa, callout, tela de estado (G6–G10) e folha (bottom sheet).
 * Tudo com `h()` (texto sempre via textContent). Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { h, icon, replace } from "../shared/components/dom.js";
import { STATUS_SCREENS } from "./logic.js";

const TOAST_MS = 5000;

/** Capa 16:9; sem URL ou com erro de carga vira o ícone de música. */
export function thumb(url, cls = "") {
  const box = h("div", { class: ["thumb", cls] }, icon("music"));
  if (url) {
    const img = h("img", { src: url, alt: "", loading: "lazy", decoding: "async", width: 64, height: 36 });
    img.addEventListener("error", () => img.remove(), { once: true });
    box.append(img);
  }
  return box;
}

/** Callout (info | warning | danger) com texto e ações opcionais. */
export function callout(tone, iconId, text, actions = [], attrs = {}) {
  return h(
    "div",
    { class: ["callout", `callout-${tone}`], ...attrs },
    icon(iconId),
    h(
      "div",
      { class: "callout-body" },
      h("p", {}, text),
      actions.length ? h("div", { class: "act" }, actions) : null,
    ),
  );
}

/** Ícone girando (spinner) para botões "Entrando…" / "Pedindo…". */
export function spinner() {
  return icon("loader", "spin");
}

/** Toast único na base da tela (role=status), com ação opcional à direita. */
export function createToaster(region) {
  let timer = 0;
  const hide = () => {
    clearTimeout(timer);
    replace(region);
  };
  return {
    show(text, { action = null, iconId = "check", tone = "success" } = {}) {
      clearTimeout(timer);
      const btn = action
        ? h("button", {
          type: "button",
          class: "btn btn-ghost",
          onclick: () => {
            hide();
            action.onClick();
          },
        }, action.label)
        : null;
      replace(
        region,
        h("div", { class: ["toast", `toast-${tone}`] }, icon(iconId), h("span", {}, text), btn),
      );
      timer = setTimeout(hide, TOAST_MS);
    },
    hide,
  };
}

/** Tela cheia de estado (G6–G10). `onRetry` só nas telas que têm "Tentar de novo". */
export function statusScreen(kind, onRetry) {
  const s = STATUS_SCREENS[kind] || STATUS_SCREENS.offline;
  const retry = s.retry && onRetry
    ? h(
      "button",
      {
        type: "button",
        class: ["btn", "btn-lg", s.retry === "primary" ? "btn-primary" : "btn-secondary"],
        onclick: onRetry,
      },
      icon("refresh"),
      "Tentar de novo",
    )
    : null;
  return h(
    "div",
    { class: "state" },
    h(
      "div",
      { class: "empty" },
      h("div", { class: ["ring", s.tone] }, icon(s.icon)),
      h("h1", { tabindex: "-1", id: "state-title" }, s.title),
      h("p", {}, s.text),
      retry,
      s.small ? h("p", { class: "small" }, s.small) : null,
    ),
  );
}

/**
 * Abre uma folha (bottom sheet, role=dialog) dentro de `host`. O resto da página fica `inert` (prende o foco).
 * Fecha com Esc, toque fora, arrastar a alça para baixo ou `close()`. Devolve o foco a `opener`.
 * @returns {{el: HTMLElement, close: () => void}}
 */
export function openSheet({ host, inertRoot, labelledBy, content, opener, onClose = () => {} }) {
  const scrim = h("div", { class: "scrim" });
  const grab = h("div", { class: "grab", "aria-hidden": "true" });
  const el = h(
    "div",
    { class: "sheet", role: "dialog", "aria-modal": "true", "aria-labelledby": labelledBy },
    grab,
  );
  el.append(...[content].flat());
  let open = true;

  const close = () => {
    if (!open) return;
    open = false;
    document.removeEventListener("keydown", onKey, true);
    scrim.remove();
    el.remove();
    inertRoot.inert = false;
    onClose();
    if (opener && opener.isConnected) opener.focus({ preventScroll: true });
  };
  const onKey = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  };

  // Arrastar para baixo (alça ou topo da folha) fecha a partir de 80 px.
  let startY = null;
  el.addEventListener("touchstart", (e) => {
    const t = e.target;
    if (t === grab || t === el || (t.closest && t.closest(".sheet-head"))) startY = e.touches[0].clientY;
  }, { passive: true });
  el.addEventListener("touchmove", (e) => {
    if (startY === null) return;
    const dy = Math.max(0, e.touches[0].clientY - startY);
    el.style.setProperty("transform", `translateY(${dy}px)`);
  }, { passive: true });
  el.addEventListener("touchend", (e) => {
    if (startY === null) return;
    const dy = e.changedTouches[0].clientY - startY;
    startY = null;
    el.style.removeProperty("transform");
    if (dy > 80) close();
  });

  scrim.addEventListener("click", close);
  document.addEventListener("keydown", onKey, true);
  inertRoot.inert = true;
  host.append(scrim, el);
  return { el, close };
}
