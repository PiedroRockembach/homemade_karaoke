/**
 * Suavização da altura cantada (US-25, ciclo 20261021-0030-karaoke). Pura, sem DOM: recebe a leitura crua
 * do detector a cada quadro e devolve a altura estável que o indicador desenha e a pontuação usa.
 *
 * Etapas, nesta ordem:
 * 1. Histerese sonoro/silêncio: entra em "sonoro" com nível e confiança altos (`*_ON`) e só sai abaixo
 *    de limiares mais baixos (`*_OFF`), sem piscar quando a voz oscila perto do limiar.
 * 2. Rejeição de salto: leitura a mais de `JUMP_CENTS` do valor atual (erro de oitava, ruído) só vale depois
 *    de `JUMP_CONFIRM` leituras seguidas parecidas. Saltos isolados de 1–2 leituras são descartados.
 * 3. Mediana das últimas `MEDIAN_N` leituras (tira espinhos menores).
 * 4. Média exponencial em cents, por tempo (independe do FPS): lenta perto do valor (tira o tremor); rápida
 *    quando a mediana se afasta `SLOW_BAND_CENTS`, até voltar a menos de `SETTLE_CENTS` (transições reais de
 *    nota aparecem em menos de ~80 ms).
 * 5. Ao calar: mantém o último valor por `HOLD_MS` e some em `FADE_MS`. Nesse intervalo `voiced` já é
 *    false, e silêncio nunca pontua.
 *
 * Dono: frontend-engineer (T-001, ciclo 20261021-0030-karaoke).
 */

export const RMS_ON = 0.012;
export const RMS_OFF = 0.007;
export const CONF_ON = 0.8;
export const CONF_OFF = 0.6;
export const JUMP_CENTS = 500;
export const JUMP_CONFIRM = 3;
export const CONFIRM_TOL_CENTS = 100;
export const MEDIAN_N = 3;
export const SLOW_BAND_CENTS = 60;
export const SETTLE_CENTS = 15; // modo rápido segue até chegar a menos disso do valor (histerese)
export const SLOW_TAU_MS = 70;
export const FAST_TAU_MS = 15;
export const HOLD_MS = 120;
export const FADE_MS = 150;
const MAX_DT_MS = 100;

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

export class PitchSmoother {
  constructor() {
    this.reset();
  }

  reset() {
    this.value = null; // MIDI contínuo exibido
    this.voiced = false;
    this.history = [];
    this.candidate = null; // {midi, count}: possível salto grande aguardando confirmação
    this.fast = false; // média rápida (transição em curso)
    this.lastT = null;
    this.lastVoicedAt = -Infinity;
  }

  /**
   * @param {{midi: number|null, confidence: number, rms: number}|null} reading leitura crua (null = silêncio)
   * @param {number} nowMs relógio em ms
   * @returns {{midi: number|null, voiced: boolean, alpha: number}} alpha 0..1 = opacidade do indicador
   */
  update(reading, nowMs) {
    const dt = this.lastT == null ? 0 : Math.max(0, Math.min(MAX_DT_MS, nowMs - this.lastT));
    this.lastT = nowMs;
    const r = reading || { midi: null, confidence: 0, rms: 0 };
    const tonal = Number.isFinite(r.midi);
    this.voiced = tonal &&
      (this.voiced
        ? r.rms >= RMS_OFF && r.confidence >= CONF_OFF
        : r.rms >= RMS_ON && r.confidence >= CONF_ON);

    if (!this.voiced) {
      this.history = [];
      this.candidate = null;
      const since = nowMs - this.lastVoicedAt;
      if (this.value == null || since >= HOLD_MS + FADE_MS) {
        this.value = null;
        return { midi: null, voiced: false, alpha: 0 };
      }
      const alpha = since <= HOLD_MS ? 1 : 1 - (since - HOLD_MS) / FADE_MS;
      return { midi: this.value, voiced: false, alpha };
    }

    this.lastVoicedAt = nowMs;
    const m = r.midi;
    if (this.value == null) return this.snap(m);

    if (Math.abs(m - this.value) * 100 > JUMP_CENTS) {
      const c = this.candidate;
      if (c && Math.abs(m - c.midi) * 100 <= CONFIRM_TOL_CENTS) {
        c.count++;
        c.midi = m;
      } else this.candidate = { midi: m, count: 1 };
      if (this.candidate.count >= JUMP_CONFIRM) return this.snap(m);
      return { midi: this.value, voiced: true, alpha: 1 }; // ainda não confirmado: ignora a leitura
    }
    this.candidate = null;

    this.history.push(m);
    if (this.history.length > MEDIAN_N) this.history.shift();
    const med = median(this.history);
    const dev = Math.abs(med - this.value) * 100;
    if (dev >= SLOW_BAND_CENTS) this.fast = true;
    else if (dev < SETTLE_CENTS) this.fast = false;
    const tau = this.fast ? FAST_TAU_MS : SLOW_TAU_MS;
    this.value += (med - this.value) * (1 - Math.exp(-dt / tau));
    return { midi: this.value, voiced: true, alpha: 1 };
  }

  snap(m) {
    this.value = m;
    this.history = [m];
    this.candidate = null;
    return { midi: m, voiced: true, alpha: 1 };
  }
}
