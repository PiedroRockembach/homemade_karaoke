/**
 * UI transversal do Controle: toasts (com Desfazer), modais acessíveis (trap de foco, Esc, clique fora,
 * foco volta a quem abriu) e utilitário de botão em carregamento.
 *
 * Dono: frontend-engineer (T-008).
 */

import { h, icon } from "../shared/components/dom.js";

const TOAST_MS = 5000;

/**
 * Mostra um toast no canto inferior direito.
 * @param {string} message
 * @param {{level?: "success"|"info"|"warning"|"error", undo?: () => void, duration?: number,
 *          actions?: {label: string, iconId?: string, onclick: () => void}[]}} [opts]
 *   `actions`: botões extras (ex.: "Conferir"); clicar fecha o toast.
 */
export function toast(message, { level = "success", undo = null, duration = TOAST_MS, actions = [] } = {}) {
  const root = document.getElementById("toasts");
  if (!root) return;
  const iconId = level === "error"
    ? "alert"
    : level === "warning"
    ? "alert"
    : level === "info"
    ? "music"
    : "check";
  let timer = 0;
  const kill = () => {
    clearTimeout(timer);
    el.remove();
  };
  const el = h(
    "div",
    { class: ["toast", `toast-${level}`], role: level === "error" ? "alert" : "status" },
    icon(iconId, "icon-sm"),
    h("span", { class: "grow" }, message),
    undo
      ? h(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          onclick: () => {
            kill();
            undo();
          },
        },
        icon("undo", "icon-sm"),
        "Desfazer",
      )
      : null,
    actions.map((a) =>
      h(
        "button",
        {
          class: "btn btn-ghost",
          type: "button",
          onclick: () => {
            kill();
            a.onclick();
          },
        },
        a.iconId ? icon(a.iconId, "icon-sm") : null,
        a.label,
      )
    ),
    h(
      "button",
      {
        class: "btn btn-ghost btn-icon",
        type: "button",
        "aria-label": "Fechar aviso",
        title: "Fechar",
        onclick: kill,
      },
      icon("x", "icon-sm"),
    ),
  );
  // Pausa o tempo enquanto o mouse/foco está no toast (dá tempo de clicar em Desfazer).
  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(kill, duration);
  };
  el.addEventListener("mouseenter", () => clearTimeout(timer));
  el.addEventListener("mouseleave", arm);
  el.addEventListener("focusin", () => clearTimeout(timer));
  el.addEventListener("focusout", arm);
  root.append(el);
  while (root.children.length > 4) root.firstElementChild.remove();
  arm();
}

/** Toast de erro a partir de um ApiError (mensagem pt-BR do servidor). */
export function toastError(err, fallback = "Algo deu errado.") {
  toast((err && err.message) || fallback, { level: "error", duration: 7000 });
}

let openModalState = null;

/**
 * Abre um modal.
 * @param {{title: string, subtitle?: string|Node, body?: Node|Node[], footer?: (close: () => void) => Node[],
 *          wide?: boolean, initialFocus?: string, onClose?: () => void}} opts
 * @returns {{close: () => void, el: HTMLElement, setFooter: (nodes: Node[]) => void}}
 */
export function openModal(
  { title, subtitle = null, body = null, footer = null, wide = false, initialFocus = null, onClose },
) {
  if (openModalState) openModalState.close(false);
  const root = document.getElementById("modal-root");
  const lastFocus = document.activeElement;
  const titleId = "modal-title";
  const foot = h("div", { class: "modal-foot" });
  const modal = h(
    "div",
    { class: ["modal", wide && "wide"], role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h(
      "div",
      { class: "modal-head" },
      h("div", { class: "grow" }, h("h2", { id: titleId }, title), subtitle ? h("p", {}, subtitle) : null),
      h("button", {
        class: "btn btn-ghost btn-icon",
        type: "button",
        "aria-label": "Fechar",
        title: "Fechar (Esc)",
        onclick: () => close(),
      }, icon("x")),
    ),
    body ? h("div", { class: "modal-body" }, body) : null,
    foot,
  );
  const bg = h("div", { class: "modal-bg" }, modal);
  bg.addEventListener("mousedown", (e) => {
    if (e.target === bg) close();
  });
  modal.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      close();
    } else if (e.key === "Tab") {
      const f = [
        ...modal.querySelectorAll("button, input, textarea, select, [tabindex]:not([tabindex='-1'])"),
      ]
        .filter((x) => !x.disabled && x.offsetParent !== null);
      if (!f.length) return;
      const a = f[0];
      const z = f[f.length - 1];
      if (e.shiftKey && document.activeElement === a) {
        e.preventDefault();
        z.focus();
      } else if (!e.shiftKey && document.activeElement === z) {
        e.preventDefault();
        a.focus();
      }
    }
  });

  function close(restore = true) {
    if (!bg.isConnected) return;
    bg.remove();
    document.body.classList.remove("has-modal");
    if (openModalState && openModalState.el === modal) openModalState = null;
    if (onClose) onClose();
    if (restore && lastFocus && lastFocus.isConnected) lastFocus.focus();
  }
  const setFooter = (nodes) => foot.replaceChildren(...nodes);
  if (footer) setFooter(footer(close));
  root.replaceChildren(bg);
  document.body.classList.add("has-modal");
  const focusEl = (initialFocus && modal.querySelector(initialFocus)) ||
    modal.querySelector("input:not([type=radio]):not([disabled]), textarea") ||
    foot.querySelector(".btn-primary") ||
    // Ação destrutiva: foco inicial em "Cancelar" (Enter não apaga por engano).
    (foot.querySelector(".btn-danger") && foot.querySelector(".btn-ghost")) ||
    modal.querySelector("button");
  if (focusEl) focusEl.focus();
  openModalState = { close, el: modal, setFooter };
  return openModalState;
}

/** true se há modal aberto. */
export function modalOpen() {
  return openModalState !== null;
}

/**
 * Executa `fn` com o botão em estado de carregamento (desabilitado + aria-busy). Retorna o resultado.
 * @template T
 * @param {HTMLButtonElement|null} btn
 * @param {() => Promise<T>} fn
 */
export async function withBusy(btn, fn) {
  if (btn) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    btn.classList.add("is-busy");
  }
  try {
    return await fn();
  } finally {
    if (btn && btn.isConnected) {
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
      btn.classList.remove("is-busy");
    }
  }
}

/** Botão de rodapé de modal. */
export function modalButton(
  label,
  { variant = "ghost", iconId = null, onclick, type = "button", disabled = false } = {},
) {
  return h(
    "button",
    { class: ["btn", `btn-${variant}`], type, onclick, disabled },
    iconId ? icon(iconId) : null,
    label,
  );
}
