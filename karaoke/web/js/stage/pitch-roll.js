/**
 * Guia de notas (pitch roll) do Palco (US-18, DESIGN §13.2).
 *
 * Canvas redesenhado a cada quadro pelo relógio do áudio: barras = notas (altura MIDI no eixo vertical,
 * duração no horizontal) que correm da direita para a esquerda até a linha "Now"; ponto luminoso = voz do
 * cantor. Cores vêm dos tokens `--stage-note-*`/`--stage-pitch-trail` (lidos uma vez do CSS).
 *
 * Dono: frontend-engineer (T-004, ciclo 20261017-1115-karaoke).
 */

const WINDOW_PRE_S = 1.0; // segundos visíveis antes do "Now"
const WINDOW_POST_S = 3.5; // segundos visíveis depois (próximas notas)
const NOW_AT = 0.22; // posição do "Now" (fração da largura)
const MIN_MIDI = 48; // C3
const MAX_MIDI = 83; // B5 (AC-18.2)
const MIN_SPAN = 12;

/** Deslocamentos de oitava da régua (US-27), na ordem da tecla O: 0 → −1 → +1 → 0. */
export const OCTAVE_SHIFTS = [0, -1, 1];

/** Próximo deslocamento da tecla O. Valor desconhecido → 0. */
export function nextOctaveShift(shift) {
  const i = OCTAVE_SHIFTS.indexOf(shift);
  return OCTAVE_SHIFTS[(i + 1) % OCTAVE_SHIFTS.length] ?? 0;
}

/** Normaliza um valor salvo (localStorage) para −1, 0 ou +1. */
export function parseOctaveShift(raw) {
  const n = Number(raw);
  return OCTAVE_SHIFTS.includes(n) ? n : 0;
}

/** Texto do HUD para o deslocamento. */
export function octaveShiftLabel(shift) {
  if (shift < 0) return "Régua: 1 oitava abaixo";
  if (shift > 0) return "Régua: 1 oitava acima";
  return "Régua: oitava original";
}

/**
 * Faixa vertical (MIDI) para as notas da música, já deslocadas `shift` oitavas: justa às notas com
 * 2 semitons de folga, ≥ 1 oitava, dentro de 36–84 (faixa do backend) estendida pelo deslocamento. Pura.
 */
export function midiRange(notes, shift = 0) {
  const lo = 36 + 12 * Math.min(0, shift);
  const hi = 84 + 12 * Math.max(0, shift);
  const midis = (notes || []).filter((n) => n.midi > 0).map((n) => n.midi + 12 * shift);
  if (!midis.length) return { min: MIN_MIDI, max: MAX_MIDI };
  let min = Math.max(lo, Math.min(...midis) - 2);
  let max = Math.min(hi, Math.max(...midis) + 2);
  if (max - min < MIN_SPAN) {
    const pad = Math.ceil((MIN_SPAN - (max - min)) / 2);
    min = Math.max(lo, min - pad);
    max = min + MIN_SPAN;
    if (max > hi) [min, max] = [hi - MIN_SPAN, hi];
  }
  return { min, max };
}

function readTokens() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  return {
    bar: v("--stage-note-bar", "#38bdf8"),
    hit: v("--stage-note-hit", "#facc15"),
    perfect: v("--stage-note-perfect", "#4ade80"),
    trail: v("--stage-pitch-trail", "#ec4899"),
    line: v("--stage-text", "#ffffff"),
    ink: v("--stage-bg", "#07050f"),
    font: v("--font-sans", "system-ui, sans-serif"),
  };
}

export class PitchRoll {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {HTMLElement} box
   */
  constructor(canvas, box) {
    this.canvas = canvas;
    this.box = box;
    this.ctx = canvas.getContext("2d");
    this.notes = [];
    this.shift = 0;
    this.range = midiRange([]);
    this.particles = [];
    this.octLabel = box.querySelector("[data-pitch-oct]");
    this.colors = null;
    this.reduceMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    this.size = null; // {w, h} em px CSS, via ResizeObserver (sem ler layout a cada quadro)
    if (globalThis.ResizeObserver) {
      new ResizeObserver(([e]) => {
        this.size = { w: e.contentRect.width, h: e.contentRect.height };
      }).observe(canvas);
    }
    this.cursor = 0; // índice da primeira nota possivelmente visível
  }

  /** Primeira nota com `end` ≥ t (cursor que só anda para frente; seek para trás reinicia por busca binária). */
  firstVisible(t) {
    const notes = this.notes;
    let i = this.cursor;
    if (i > 0 && i <= notes.length && notes[i - 1].end >= t) {
      let lo = 0;
      let hi = i;
      while (lo < hi) {
        const mid = (lo + hi) >> 1;
        if (notes[mid].end < t) lo = mid + 1;
        else hi = mid;
      }
      i = lo;
    }
    while (i < notes.length && notes[i].end < t) i++;
    return (this.cursor = i);
  }

  /** Notas da música atual (ordenadas por `start`), ou [] para limpar. */
  setNotes(notes) {
    this.notes = Array.isArray(notes) ? notes : [];
    this.range = midiRange(this.notes, this.shift);
    this.particles = [];
    this.cursor = 0;
  }

  /** Desloca as barras ±1 oitava (US-27) e mostra o rótulo discreto quando ≠ 0. */
  setOctaveShift(shift) {
    this.shift = shift;
    this.range = midiRange(this.notes, shift);
    if (this.octLabel) {
      this.octLabel.textContent = shift < 0 ? "−1 oitava" : shift > 0 ? "+1 oitava" : "";
      this.octLabel.hidden = !shift;
    }
  }

  setVisible(visible) {
    this.box.hidden = !visible;
  }

  /**
   * Desenha um quadro.
   * @param {number} timeS posição do áudio (s)
   * @param {number|null} userMidi altura **real** cantada, filtrada (MIDI contínuo), null = sem voz
   * @param {object|null} target nota esperada agora
   * @param {boolean} isHit cantor dentro da tolerância
   * @param {number} userAlpha opacidade do indicador (some suave ao calar)
   */
  render(timeS, userMidi, target, isHit, userAlpha = 1) {
    if (this.box.hidden) return;
    const rect = this.size && this.size.w
      ? { width: this.size.w, height: this.size.h }
      : this.canvas.getBoundingClientRect();
    const dpr = globalThis.devicePixelRatio || 1;
    const w = Math.floor(rect.width * dpr);
    const h = Math.floor(rect.height * dpr);
    if (!w || !h) return;
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w;
      this.canvas.height = h;
    }
    const col = this.colors || (this.colors = readTokens());
    const ctx = this.ctx;
    const cssW = rect.width;
    const cssH = rect.height;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const { min, max } = this.range;
    const rowH = cssH / (max - min + 1);
    const yOf = (midi) => cssH - (midi - min + 0.5) * rowH; // centro da linha da nota
    const nowX = cssW * NOW_AT;
    const pxPerSec = (cssW - nowX) / WINDOW_POST_S;

    // Grade: linha fina em cada Dó (oitava).
    ctx.strokeStyle = col.line;
    ctx.globalAlpha = 0.07;
    ctx.lineWidth = 1;
    for (let m = min; m <= max; m++) {
      if (m % 12 !== 0) continue;
      const y = Math.round(yOf(m)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(cssW, y);
      ctx.stroke();
    }

    // Linha "Now".
    ctx.globalAlpha = 0.3;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(nowX, 0);
    ctx.lineTo(nowX, cssH);
    ctx.stroke();
    ctx.globalAlpha = 1;

    // Barras.
    const barH = Math.max(6, Math.min(rowH * 0.8, Math.max(22, cssH * 0.1))); // acompanha a altura da régua
    const fontPx = Math.max(10, Math.min(14, barH - 4));
    ctx.font = `bold ${fontPx}px ${col.font}`;
    ctx.textBaseline = "middle";
    const minT = timeS - WINDOW_PRE_S - 0.5;
    const maxT = timeS + WINDOW_POST_S;
    for (let i = this.firstVisible(minT); i < this.notes.length; i++) {
      const note = this.notes[i];
      if (note.start > maxT) break;
      const x = nowX + (note.start - timeS) * pxPerSec;
      const barW = Math.max(6, (note.end - note.start) * pxPerSec);
      const y = yOf(note.midi + 12 * this.shift) - barH / 2;
      const active = note === target;
      const lit = active && isHit;
      ctx.fillStyle = lit ? col.hit : col.bar;
      ctx.globalAlpha = active ? 1 : note.end < timeS ? 0.35 : 0.7;
      ctx.shadowColor = lit ? col.hit : col.bar;
      ctx.shadowBlur = lit ? 14 : active ? 6 : 0;
      ctx.beginPath();
      ctx.roundRect(x, y, barW, barH, Math.min(6, barH / 2));
      ctx.fill();
      ctx.shadowBlur = 0;
      if (note.text && barW > 28 && barH >= 12) {
        ctx.fillStyle = col.ink;
        ctx.fillText(note.text, x + 5, y + barH / 2, barW - 10);
      }
    }
    ctx.globalAlpha = 1;

    // Voz do cantor no "Now".
    if (userMidi != null && userAlpha > 0) {
      // Fora da faixa: preso na borda como anel vazado (dica para usar a tecla O), nunca dobrado de oitava.
      const outside = userMidi < min - 0.5 || userMidi > max + 0.5;
      const y = yOf(Math.max(min - 0.5, Math.min(max + 0.5, userMidi)));
      const color = isHit ? col.perfect : col.trail;
      ctx.globalAlpha = userAlpha;
      ctx.fillStyle = color;
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = 16;
      ctx.beginPath();
      ctx.arc(nowX, Math.max(7, Math.min(cssH - 7, y)), isHit ? 8 : 6, 0, Math.PI * 2);
      if (outside) {
        ctx.lineWidth = 2;
        ctx.stroke();
      } else ctx.fill();
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
      if (isHit && !this.reduceMotion && Math.random() < 0.4) {
        const a = Math.random() * Math.PI * 2;
        this.particles.push({
          x: nowX,
          y,
          vx: Math.cos(a) * 40,
          vy: Math.sin(a) * 40,
          alpha: 1,
          color: Math.random() < 0.5 ? col.hit : col.perfect,
        });
      }
    }

    // Faíscas de acerto.
    for (let i = this.particles.length - 1; i >= 0; i--) {
      const p = this.particles[i];
      p.x += p.vx * 0.016;
      p.y += p.vy * 0.016;
      p.alpha -= 0.04;
      if (p.alpha <= 0) {
        this.particles.splice(i, 1);
        continue;
      }
      ctx.globalAlpha = p.alpha;
      ctx.fillStyle = p.color;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
