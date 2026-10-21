/**
 * Pontuação e avaliação de afinação do cantor (US-19; estrita desde US-26, ciclo 20261021-0030).
 *
 * Lógica pura (testável sem DOM): `checkPitchHit`, `noteAt`, `rankFor`, `pointsPerSecond`, `comboWeight`.
 * A classe `Scorer` só acumula pontos e atualiza o placar/cartão final de `stage.html`.
 *
 * Dono: frontend-engineer (T-001, ciclo 20261021-0030-karaoke).
 */

export const MAX_SCORE = 10000;
/** Tolerância de acerto em cents (meio semitom), sobre a altura contínua, sem equivalência de oitava. */
export const HIT_CENTS = 50;

/**
 * O cantor está na nota? |cantado − alvo| ≤ 50 cents em MIDI contínuo (sem arredondar).
 * O alvo já vem na oitava da régua (deslocamento manual, US-27): uma oitava acima/abaixo **não** vale.
 * @param {number|null} targetMidi nota esperada (MIDI, já deslocada)
 * @param {number|null} userMidi altura cantada filtrada (MIDI contínuo)
 */
export function checkPitchHit(targetMidi, userMidi) {
  if (!Number.isFinite(targetMidi) || !Number.isFinite(userMidi) || targetMidi <= 0 || userMidi <= 0) {
    return false;
  }
  return Math.abs(userMidi - targetMidi) * 100 <= HIT_CENTS + 1e-9;
}

/**
 * Nota esperada no instante `t` (s). `notes` ordenadas por `start`; busca binária.
 * @param {Array<{start:number,end:number,midi:number}>} notes
 * @param {number} t
 */
export function noteAt(notes, t) {
  let lo = 0;
  let hi = notes.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const n = notes[mid];
    if (t < n.start) hi = mid - 1;
    else if (t > n.end) lo = mid + 1;
    else return n;
  }
  return null;
}

/** Combo (em segundos afinados seguidos) que leva o peso do quadro ao máximo. */
export const COMBO_FULL_S = 2;
/** Peso de um segundo afinado sem combo; com combo cheio o peso é 1. */
export const BASE_WEIGHT = 0.9;

/**
 * Pontos por segundo afinado com combo cheio: 100% do tempo de notas afinado ≈ 10.000. A pontuação é
 * proporcional ao tempo de nota acertado ÷ tempo total de notas (ADR-0004 §2.3), com até 10% de bônus de
 * sequência. Mínimo de 20 s de notas para faixas quase vazias não darem pontos demais.
 */
export function pointsPerSecond(totalNotesSec) {
  return MAX_SCORE / Math.max(20, totalNotesSec);
}

/** Peso médio de um passo de `dt` s começando com `comboSec` de sequência (integração exata, sem FPS). */
export function comboWeight(comboSec, dt) {
  const full = Math.max(0, comboSec + dt - COMBO_FULL_S); // parte do passo já com combo cheio
  const ramp = dt - Math.min(dt, full); // parte ainda na rampa
  const c0 = Math.min(comboSec, COMBO_FULL_S);
  const rampArea = ramp * (BASE_WEIGHT + ((1 - BASE_WEIGHT) * (c0 + ramp / 2)) / COMBO_FULL_S);
  return dt > 0 ? (rampArea + Math.min(dt, full)) / dt : 0;
}

/** Mensagem de desempenho do cartão final. */
export function rankFor(points) {
  if (points >= 9000) return "Lendário! Afinação Perfeita!";
  if (points >= 7500) return "Sensacional! Que show!";
  if (points >= 5000) return "Muito Bom! Quase um profissional!";
  if (points >= 2500) return "Bom show! Continue praticando!";
  return "Valeu a tentativa! O importante é a festa!";
}

const fmt = (n) => Math.round(n).toLocaleString("pt-BR");

export class Scorer {
  /** @param {HTMLElement} root elemento principal do Palco */
  constructor(root) {
    this.scoreBox = root.querySelector("[data-score]");
    this.scoreNum = root.querySelector("[data-score-num]");
    this.overlay = root.querySelector("[data-score-overlay]");
    this.finalVal = root.querySelector("[data-score-final-val]");
    this.finalRank = root.querySelector("[data-score-final-rank]");
    this.reduceMotion = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    this.reset([]);
  }

  /** Zera para uma nova música. */
  reset(notes) {
    this.score = 0;
    this.displayScore = 0;
    this.comboSec = 0;
    this.scoredUntil = -Infinity;
    this.pps = pointsPerSecond(notes.reduce((acc, n) => acc + (n.end - n.start), 0));
    this.paint(true);
  }

  /**
   * Soma os pontos de um passo de `deltaSec` (independente do FPS). Fora de nota (`targetMidi` nulo) o combo
   * se mantém; errar durante uma nota o zera. Trecho já pontuado (seek para trás, `timeS` < já visto) não soma.
   * @returns {boolean} acerto neste passo
   */
  tick(targetMidi, userMidi, deltaSec, timeS = null) {
    const hit = checkPitchHit(targetMidi, userMidi);
    if (targetMidi == null) {
      this.paint(false, deltaSec);
      return false;
    }
    const fresh = timeS == null || timeS > this.scoredUntil;
    if (timeS != null) this.scoredUntil = Math.max(this.scoredUntil, timeS);
    if (hit) {
      if (fresh) {
        const gain = this.pps * deltaSec * comboWeight(this.comboSec, deltaSec);
        this.score = Math.min(MAX_SCORE, this.score + gain);
      }
      this.comboSec += deltaSec;
    } else this.comboSec = 0;
    this.paint(false, deltaSec);
    return hit;
  }

  /** Placar com incremento suave por tempo (instantâneo com movimento reduzido). */
  paint(instant, dt = 0.016) {
    if (instant || this.reduceMotion) this.displayScore = this.score;
    else this.displayScore += (this.score - this.displayScore) * (1 - Math.exp(-dt * 12));
    if (Math.abs(this.score - this.displayScore) < 0.5) this.displayScore = this.score;
    if (this.scoreNum) this.scoreNum.textContent = fmt(this.displayScore);
  }

  /** Placar do topo (o cartão final é controlado à parte por `showFinal`/`hideFinal`). */
  setVisible(visible) {
    if (this.scoreBox) this.scoreBox.hidden = !visible;
  }

  /**
   * Mostra o cartão final ("8.540 pts — …"). `compact` = faixa no topo da cena "A seguir" (não cobre o
   * próximo cantor nem o anel); senão, cartão de tela cheia (sem próxima música). Duração: quem chama.
   * @returns {string} texto para anunciar a leitores de tela
   */
  showFinal(points, compact) {
    const pts = Math.round(points);
    const rank = rankFor(pts);
    if (this.finalVal) this.finalVal.textContent = fmt(pts);
    if (this.finalRank) this.finalRank.textContent = rank;
    if (this.overlay) {
      this.overlay.classList.toggle("compact", !!compact);
      this.overlay.hidden = false;
    }
    return `Pontuação final: ${fmt(pts)} pontos. ${rank}`;
  }

  hideFinal() {
    if (this.overlay) this.overlay.hidden = true;
  }
}
