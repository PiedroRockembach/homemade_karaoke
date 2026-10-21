/**
 * Microfone do Palco e detecção da altura cantada (US-19; revisto em US-25/26, ciclo 20261021-0030).
 *
 * `getUserMedia` sem supressão de ruído nem ganho automático (deformam voz tonal e fazem o nível "bombear");
 * cancelamento de eco ligado (reduz a música da própria página). Detector YIN (diferença cumulativa
 * normalizada, CMND) com interpolação parabólica: devolve frequência contínua + confiança (1 − CMND).
 * A suavização/histerese fica em `pitch-smooth.js`. O áudio do microfone nunca vai à rede nem à saída de som.
 *
 * Dono: frontend-engineer (T-001, ciclo 20261021-0030-karaoke).
 */

export const MIN_FREQ = 65; // ~C2
export const MAX_FREQ = 1000; // ~B5
export const RMS_FLOOR = 0.004; // abaixo disso nem roda o detector (silêncio absoluto)
export const YIN_THRESHOLD = 0.15; // primeiro mínimo da CMND abaixo disso = período (YIN clássico)
export const MAX_CMND = 0.35; // melhor CMND acima disso = som sem altura definida (ruído, consoante)
const WINDOW = 1024; // amostras somadas por lag (~21 ms a 48 kHz)

export const MIC_CONSTRAINTS = {
  audio: { echoCancellation: true, noiseSuppression: false, autoGainControl: false },
};

let diffBuf = new Float32Array(0); // reaproveitado entre quadros

/** Frequência (Hz) → nota MIDI **contínua** (A4 = 440 Hz = 69; 0,01 = 1 cent). Inválida → null. */
export function freqToMidi(freq) {
  if (!Number.isFinite(freq) || freq <= 0) return null;
  return 69 + 12 * Math.log2(freq / 440);
}

/**
 * YIN sobre um quadro de áudio (pura, sem Web Audio).
 * @param {Float32Array} buf amostras no domínio do tempo (−1..1)
 * @param {number} sampleRate
 * @returns {{freq: number|null, rms: number, confidence: number}} confidence 0..1 (0 = sem altura)
 */
export function detectPitch(buf, sampleRate) {
  const n = buf.length;
  let energy = 0;
  for (let i = 0; i < n; i++) energy += buf[i] * buf[i];
  const rms = Math.sqrt(energy / n);
  const none = { freq: null, rms, confidence: 0 };
  if (rms < RMS_FLOOR) return none;

  const minLag = Math.max(2, Math.floor(sampleRate / MAX_FREQ));
  const maxLag = Math.min(Math.ceil(sampleRate / MIN_FREQ), n - 2);
  const w = Math.min(WINDOW, n - maxLag - 1);
  if (w < 64) return none;
  if (diffBuf.length < maxLag + 2) diffBuf = new Float32Array(maxLag + 2);
  const d = diffBuf;

  // Diferença + normalização cumulativa (CMND): d'(τ) = d(τ)·τ / Σ_{j≤τ} d(j).
  d[0] = 1;
  let running = 0;
  for (let tau = 1; tau <= maxLag + 1; tau++) {
    let sum = 0;
    for (let i = 0; i < w; i++) {
      const delta = buf[i] - buf[i + tau];
      sum += delta * delta;
    }
    running += sum;
    d[tau] = running > 0 ? (sum * tau) / running : 1;
  }

  let best = -1;
  for (let tau = minLag; tau <= maxLag; tau++) {
    if (d[tau] < YIN_THRESHOLD) {
      while (tau + 1 <= maxLag && d[tau + 1] < d[tau]) tau++;
      best = tau;
      break;
    }
  }
  if (best < 0) {
    // Sem mínimo abaixo do limiar: o menor valor, se ainda for "tonal o bastante".
    for (let tau = minLag; tau <= maxLag; tau++) if (best < 0 || d[tau] < d[best]) best = tau;
    if (d[best] > MAX_CMND) return none;
  }

  const a = d[best - 1];
  const b = d[best];
  const c = d[best + 1];
  const denom = a - 2 * b + c;
  const shift = Math.abs(denom) > 1e-12 ? (a - c) / (2 * denom) : 0;
  const lag = best + Math.max(-1, Math.min(1, shift));
  const freq = sampleRate / lag;
  if (freq < MIN_FREQ || freq > MAX_FREQ) return none;
  return { freq, rms, confidence: Math.max(0, Math.min(1, 1 - b)) };
}

export class MicPitch {
  constructor() {
    /** @type {AudioContext|null} */
    this.audioCtx = null;
    /** @type {MediaStream|null} */
    this.stream = null;
    /** @type {AnalyserNode|null} */
    this.analyser = null;
    /** @type {Float32Array|null} */
    this.buf = null;
    this.active = false;
    this.pending = false;
    /** @type {null|"denied"|"unavailable"} */
    this.error = null;
    this.gen = 0; // invalida um start() em andamento quando stop() chega antes
  }

  /** Pede o microfone e liga o analisador. Resolve `true` se ficou ativo. */
  async start() {
    if (this.active || this.pending) return this.active;
    const gen = ++this.gen;
    this.pending = true;
    let stream = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("unavailable");
      stream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
      if (gen !== this.gen) {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      const ctx = new (globalThis.AudioContext || globalThis.webkitAudioContext)();
      if (ctx.state === "suspended") await ctx.resume().catch(() => {});
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      ctx.createMediaStreamSource(stream).connect(analyser); // sem ligar ao destino: nada volta para a caixa
      Object.assign(this, { stream, audioCtx: ctx, analyser, buf: new Float32Array(analyser.fftSize) });
      this.active = true;
      this.error = null;
      if (gen !== this.gen) this.stop();
      return this.active;
    } catch (err) {
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (gen === this.gen) {
        const name = err && err.name;
        this.error = name === "NotAllowedError" || name === "SecurityError" ? "denied" : "unavailable";
      }
      return false;
    } finally {
      if (gen === this.gen) this.pending = false;
    }
  }

  /** Fecha o microfone (tracks `stop()`) e libera o AudioContext. */
  stop() {
    this.gen++;
    this.pending = false;
    this.active = false;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.audioCtx) this.audioCtx.close().catch(() => {});
    this.stream = null;
    this.audioCtx = null;
    this.analyser = null;
    this.buf = null;
  }

  /**
   * Leitura crua do quadro atual (a cada quadro de vídeo; o AnalyserNode já guarda as últimas 2048 amostras).
   * @returns {{midi: number|null, confidence: number, rms: number}|null} null = microfone desligado
   */
  read() {
    if (!this.active || !this.analyser || !this.buf) return null;
    this.analyser.getFloatTimeDomainData(this.buf);
    const r = detectPitch(this.buf, this.audioCtx.sampleRate);
    return { midi: freqToMidi(r.freq), confidence: r.confidence, rms: r.rms };
  }
}
