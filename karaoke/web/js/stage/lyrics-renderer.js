/**
 * Palco: renderizador de letra. Chamado a cada requestAnimationFrame com o tempo da letra
 * (`t = posição do AudioEngine − latência − offset`); não usa timers próprios.
 *
 * - Linha atual (grande) com preenchimento por palavra via `--p` (0..1) e linha seguinte (menor).
 * - Intervalo > 5 s / intro: três pontos + legenda com contagem, próxima linha já visível.
 * - O DOM só é reconstruído quando a linha muda; nos demais quadros só `--p` é atualizado.
 *
 * Dono: frontend-engineer (T-007).
 */

import { computeView, prepareLyrics, waitLabel, wordProgress } from "./lyrics-timing.js";

function lineEl(line, cls) {
  const p = document.createElement("p");
  p.className = "line " + cls;
  line.words.forEach((w, k) => {
    if (k > 0) p.append(" ");
    const span = document.createElement("span");
    span.className = "w";
    span.textContent = w.text;
    p.append(span);
  });
  return p;
}

export class LyricsRenderer {
  /** @param {HTMLElement} el contêiner `.lyrics` */
  constructor(el) {
    this.el = el;
    this.prepared = prepareLyrics(null);
    this.key = "";
    this.spans = [];
    this.lastP = [];
    this.dots = [];
    this.label = null;
    this.lastLabel = "";
    this.lastDots = -1;
  }

  /** Troca o documento de letra (ou null = sem letra). Retorna se há linhas. */
  setLyrics(doc) {
    this.prepared = prepareLyrics(doc);
    this.reset();
    return this.prepared.lines.length > 0;
  }

  get hasLines() {
    return this.prepared.lines.length > 0;
  }

  /** Força reconstrução no próximo quadro (seek, troca de música). */
  reset() {
    this.key = "";
    this.el.replaceChildren();
  }

  /** @param {number} t tempo da letra em ms */
  render(t) {
    this.lastT = t;
    const view = computeView(this.prepared, t);
    const { lines } = this.prepared;
    const key = view.mode + ":" + view.idx;
    if (key !== this.key) {
      this.key = key;
      this._build(view, lines);
    }
    if (view.mode === "lines") {
      const words = lines[view.idx].words;
      for (let k = 0; k < this.spans.length; k++) {
        const p = Math.round(wordProgress(words[k], t) * 1000) / 1000;
        if (p !== this.lastP[k]) {
          this.lastP[k] = p;
          this.spans[k].style.setProperty("--p", String(p));
        }
      }
    } else if (view.mode === "wait") {
      const label = waitLabel(view);
      if (label !== this.lastLabel) {
        this.lastLabel = label;
        this.label.textContent = label;
      }
      if (view.dotsOn !== this.lastDots) {
        this.lastDots = view.dotsOn;
        this.dots.forEach((d, i) => d.classList.toggle("on", i < view.dotsOn));
      }
    }
  }

  _build(view, lines) {
    this.spans = [];
    this.lastP = [];
    this.lastLabel = "";
    this.lastDots = -1;
    const nodes = [];
    if (view.mode === "lines") {
      const cur = lineEl(lines[view.idx], "cur enter");
      this.spans = Array.from(cur.querySelectorAll(".w"));
      nodes.push(cur);
      if (lines[view.idx + 1]) nodes.push(lineEl(lines[view.idx + 1], "nxt"));
    } else if (view.mode === "wait") {
      const wait = document.createElement("div");
      wait.className = "wait";
      const dots = document.createElement("div");
      dots.className = "dots";
      dots.setAttribute("aria-hidden", "true");
      this.dots = [0, 1, 2].map(() => dots.appendChild(document.createElement("i")));
      this.label = document.createElement("div");
      this.label.className = "lbl";
      wait.append(dots, this.label);
      nodes.push(wait, lineEl(lines[view.idx], "nxt enter"));
    }
    this.el.replaceChildren(...nodes);
  }
}
