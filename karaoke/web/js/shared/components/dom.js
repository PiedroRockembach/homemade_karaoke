/**
 * Utilitários de DOM sem innerHTML (títulos do YouTube são texto não confiável).
 *
 * `h("button", {class: "btn", onclick: fn, "aria-label": "x"}, icon("play"), "Tocar")`
 * - atributos `on<evento>` viram addEventListener (nada de handler inline; CSP `script-src 'self'`);
 * - `class` aceita string ou array (falsy ignorado); `dataset` aceita objeto; `style` aceita objeto de
 *   propriedades CSS (inclusive custom properties `--x`, via CSSOM);
 * - valores `false`/`null`/`undefined` omitem o atributo; `true` vira atributo vazio.
 *
 * Dono: frontend-engineer (T-008). Reutilizável pela futura página /guest.
 */

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_SPRITE = "/static/icons.svg";

/** Cria um elemento. Filhos: Node, string, número, arrays (achatados), falsy (ignorado). */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k.startsWith("on") && typeof v === "function") {
      el.addEventListener(k.slice(2), v);
    } else if (k === "class") {
      el.className = Array.isArray(v) ? v.filter(Boolean).join(" ") : v;
    } else if (k === "dataset") {
      for (const [dk, dv] of Object.entries(v)) if (dv !== undefined && dv !== null) el.dataset[dk] = dv;
    } else if (k === "style" && typeof v === "object") {
      for (const [sk, sv] of Object.entries(v)) el.style.setProperty(sk, String(sv));
    } else if (k === "value" && "value" in el) {
      el.value = v;
    } else if (k === "checked" || k === "disabled" || k === "hidden" || k === "selected") {
      el[k] = Boolean(v);
    } else {
      el.setAttribute(k, v === true ? "" : String(v));
    }
  }
  append(el, children);
  return el;
}

/** Anexa filhos (mesmas regras de `h`). */
export function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false || c === "") continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

/** Substitui todo o conteúdo de `el`. */
export function replace(el, ...children) {
  el.replaceChildren();
  return append(el, children);
}

/** Ícone do sprite (`/static/icons.svg#i-<id>`), decorativo (aria-hidden). */
export function icon(id, cls = "") {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", ("icon " + cls).trim());
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `${ICON_SPRITE}#i-${id}`);
  svg.append(use);
  return svg;
}

/**
 * Reconstrói o conteúdo de `container` com `build()` preservando o foco: o elemento focado com
 * `data-focus-key` é refocado depois (ou o vizinho mais próximo com a mesma chave base).
 */
export function preserveFocus(container, build) {
  const active = document.activeElement;
  const key = active && container.contains(active) ? active.getAttribute("data-focus-key") : null;
  build();
  if (key) {
    const again = container.querySelector(`[data-focus-key="${CSS.escape(key)}"]`);
    if (again) again.focus({ preventScroll: true });
  }
}

/** Normaliza para busca local: minúsculas e sem acentos. */
export function normalize(s) {
  return String(s || "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
}

/** Estado vazio (DESIGN §8): ícone em círculo + título + frase + ações. */
export function emptyState(
  { iconId, title, text, actions = [], compact = false, danger = false, extra = null },
) {
  return h(
    "div",
    { class: ["empty", compact && "compact"] },
    h("div", { class: ["em-icon", danger && "danger"] }, icon(iconId)),
    h("h3", {}, title),
    text ? h("p", {}, text) : null,
    extra,
    actions.length ? h("div", { class: "suggest" }, actions) : null,
  );
}
