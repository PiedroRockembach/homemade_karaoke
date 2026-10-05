/**
 * Menu suspenso acessível (DESIGN §11.3/§11.5/§11.8): `role="menu"` com itens de ação (`menuitem`) ou de escolha
 * (`menuitemradio`). ↑/↓/Home/End movem, Enter/Espaço escolhem e fecham, Esc fecha e devolve o foco ao botão,
 * Tab e clique fora fecham. O botão que abre recebe `aria-expanded`. Um menu aberto por vez.
 *
 * Dono: frontend-engineer (T-003).
 */

import { h, icon } from "../shared/components/dom.js";

let current = null;

/**
 * @typedef {{type?: "item"|"radio"|"header"|"sep"|"note", label?: string, iconId?: string, checked?: boolean,
 *            disabled?: boolean, title?: string, kbd?: string, onselect?: () => void}} MenuItem
 */

/**
 * Abre um menu ancorado em `anchor`.
 * @param {HTMLElement} anchor botão que abre (recebe aria-expanded e o foco ao fechar com Esc)
 * @param {{label: string, items: MenuItem[], align?: "start"|"end"}} opts
 */
export function openMenu(anchor, { label, items, align = "end" }) {
  const wasOpenHere = current && current.anchor === anchor;
  closeMenu(false);
  if (wasOpenHere) return;

  const buttons = [];
  const nodes = items.map((it) => {
    if (it.type === "header") return h("div", { class: "menu-h", role: "presentation" }, it.label);
    if (it.type === "sep") return h("div", { class: "menu-sep", role: "separator" });
    if (it.type === "note") return h("div", { class: "menu-note", role: "presentation" }, it.label);
    const radio = it.type === "radio";
    const btn = h(
      "button",
      {
        type: "button",
        role: radio ? "menuitemradio" : "menuitem",
        "aria-checked": radio ? String(Boolean(it.checked)) : null,
        disabled: Boolean(it.disabled),
        title: it.title || null,
        tabindex: "-1",
        onclick: () => {
          if (it.disabled) return;
          closeMenu(true);
          if (it.onselect) it.onselect();
        },
      },
      radio
        ? h("span", { class: "chk", "aria-hidden": "true" }, it.checked ? icon("check", "icon-sm") : null)
        : it.iconId
        ? icon(it.iconId, "icon-sm")
        : null,
      h("span", { class: "grow" }, it.label),
      it.kbd ? h("span", { class: "kbd", "aria-hidden": "true" }, it.kbd) : null,
    );
    buttons.push(btn);
    return btn;
  });

  const menu = h("div", { class: "menu", role: "menu", "aria-label": label }, nodes);
  document.body.append(menu);
  position(menu, anchor, align);
  anchor.setAttribute("aria-expanded", "true");

  const enabled = () => buttons.filter((b) => !b.disabled);
  const focusAt = (i) => {
    const list = enabled();
    if (!list.length) return;
    list[(i + list.length) % list.length].focus();
  };
  menu.addEventListener("keydown", (e) => {
    const list = enabled();
    const i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      focusAt(i + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      focusAt(i - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      focusAt(0);
    } else if (e.key === "End") {
      e.preventDefault();
      focusAt(list.length - 1);
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      closeMenu(true);
    } else if (e.key === "Tab") {
      closeMenu(false);
    }
  });
  const onDown = (e) => {
    if (!menu.contains(e.target) && !anchor.contains(e.target)) closeMenu(false);
  };
  const onResize = () => closeMenu(false);
  document.addEventListener("mousedown", onDown, true);
  addEventListener("resize", onResize);
  current = {
    anchor,
    menu,
    cleanup() {
      document.removeEventListener("mousedown", onDown, true);
      removeEventListener("resize", onResize);
    },
  };
  const checked = buttons.find((b) => b.getAttribute("aria-checked") === "true" && !b.disabled);
  (checked || enabled()[0] || menu).focus();
}

/** Fecha o menu aberto. `restoreFocus` devolve o foco ao botão que abriu. */
export function closeMenu(restoreFocus = false) {
  if (!current) return;
  const { anchor, menu, cleanup } = current;
  current = null;
  cleanup();
  menu.remove();
  anchor.setAttribute("aria-expanded", "false");
  if (restoreFocus && anchor.isConnected) anchor.focus();
}

/** true se há menu aberto. */
export function menuOpen() {
  return current !== null;
}

function position(menu, anchor, align) {
  if (matchMedia("(max-width: 720px)").matches) return; // folha inferior via CSS
  const r = anchor.getBoundingClientRect();
  const m = menu.getBoundingClientRect();
  const gap = 6;
  let top = r.bottom + gap;
  if (top + m.height > innerHeight - 8 && r.top - gap - m.height > 8) top = r.top - gap - m.height;
  let left = align === "end" ? r.right - m.width : r.left;
  left = Math.max(8, Math.min(left, innerWidth - m.width - 8));
  menu.style.setProperty("top", `${Math.round(top)}px`);
  menu.style.setProperty("left", `${Math.round(left)}px`);
}
