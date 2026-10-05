/**
 * Palco: HUD (pílula no topo central, 1,5 s) — DESIGN §5.3 "Sobreposições".
 * Ícone + rótulo + barra opcional (0–100) + valor. `role="status"` no HTML anuncia a mudança.
 *
 * Dono: frontend-engineer (T-007).
 */

const VISIBLE_MS = 1500;

export class Hud {
  /** @param {HTMLElement} el elemento `.hud` de stage.html */
  constructor(el) {
    this.el = el;
    this.icon = el.querySelector("[data-hud-icon]");
    this.label = el.querySelector("[data-hud-label]");
    this.bar = el.querySelector("[data-hud-bar]");
    this.fill = el.querySelector("[data-hud-fill]");
    this.value = el.querySelector("[data-hud-value]");
    this.timer = 0;
  }

  /**
   * @param {string} icon id do símbolo sem o prefixo "i-" (ex.: "vol")
   * @param {string} label
   * @param {string} [value]
   * @param {number|null} [pct] 0..100 para mostrar a barra
   */
  show(icon, label, value = "", pct = null) {
    this.icon.setAttribute("href", "#i-" + icon);
    this.label.textContent = label;
    this.value.textContent = value;
    this.value.hidden = !value;
    this.bar.hidden = pct == null;
    if (pct != null) this.fill.style.setProperty("width", Math.max(0, Math.min(100, pct)) + "%");
    this.el.classList.add("on");
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.el.classList.remove("on"), VISIBLE_MS);
  }
}
