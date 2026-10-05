/**
 * Palco: AudioEngine (Web Audio) — dono do relógio de reprodução (ARCHITECTURE §5).
 *
 *  instrumental ─► BufferSource ─► Gain(1) ────┐
 *                                              ├─► Gain(master = volume²) ─► destination
 *  vocals ──────► BufferSource ─► Gain(guide) ─┘
 *
 * - As duas fontes recebem `start(when, offset)` com o MESMO `when` → alinhadas amostra a amostra.
 * - Posição = offset + (ctx.currentTime − startedAt); em pausa fica congelada. Nada de timers.
 * - Volume/voz guia com `setTargetAtTime` (sem cliques). Seek = recria as fontes (buffers em memória).
 * - Guarda no máximo 2 músicas decodificadas (atual + próxima).
 *
 * Dono: frontend-engineer (T-007).
 */

const START_DELAY_S = 0.03; // margem para o agendamento do start
const GAIN_TIME_CONSTANT = 0.03;
const MAX_CACHED = 2;

/** Carregador padrão: fetch + decodeAudioData. */
async function defaultFetchBuffer(url, ctx) {
  const res = await fetch(url);
  if (!res.ok) {
    const err = new Error("Arquivo de áudio indisponível (HTTP " + res.status + ").");
    err.code = "media_unavailable";
    throw err;
  }
  const data = await res.arrayBuffer();
  try {
    return await ctx.decodeAudioData(data);
  } catch {
    const err = new Error("Não foi possível decodificar o áudio.");
    err.code = "decode_failed";
    throw err;
  }
}

export class AudioEngine {
  /** @param {{fetchBuffer?: (url: string, ctx: BaseAudioContext) => Promise<AudioBuffer>}} [opts] */
  constructor(opts = {}) {
    this.fetchBuffer = opts.fetchBuffer || defaultFetchBuffer;
    this.ctx = null;
    this.cache = new Map(); // key → Promise<{instrumental, vocals}>
    this.cacheOrder = [];
    this.track = null; // {key, instrumental, vocals}
    this.sources = null; // {inst, voc}
    this.playing = false;
    this.offsetSec = 0; // posição quando parado / no último start
    this.startedAt = 0; // ctx.currentTime do último start (já com o atraso)
    this.volume = 0.8;
    this.guide = 0;
    /** @type {() => void} */
    this.onEnded = () => {};
  }

  /** Cria/resume o AudioContext. Chamar dentro do gesto do usuário (Enter/clique). */
  async unlock() {
    if (!this.ctx) {
      const Ctx = globalThis.AudioContext || globalThis.webkitAudioContext;
      this.ctx = new Ctx({ latencyHint: "playback" });
      this.master = this.ctx.createGain();
      this.instGain = this.ctx.createGain();
      this.vocGain = this.ctx.createGain();
      this.master.gain.value = this.volume * this.volume;
      this.vocGain.gain.value = this.guide;
      this.instGain.connect(this.master);
      this.vocGain.connect(this.master);
      this.master.connect(this.ctx.destination);
    }
    if (this.ctx.state !== "running") await this.ctx.resume();
    return this.ctx.state === "running";
  }

  get unlocked() {
    return !!this.ctx && this.ctx.state === "running";
  }

  /** Latência de saída em segundos (lyrics-format §4). */
  get outputLatencySec() {
    if (!this.ctx) return 0;
    return this.ctx.outputLatency || this.ctx.baseLatency || 0;
  }

  /**
   * Baixa e decodifica as duas faixas (com cache). `media` = Song.media do contrato.
   * @returns {Promise<{instrumental: AudioBuffer, vocals: AudioBuffer|null}>}
   */
  load(key, media) {
    if (this.cache.has(key)) return this.cache.get(key);
    if (!this.ctx) return Promise.reject(new Error("AudioContext ainda não liberado (falta o gesto)."));
    const p = (async () => {
      const [instrumental, vocals] = await Promise.all([
        this.fetchBuffer(media.instrumental, this.ctx),
        media.vocals ? this.fetchBuffer(media.vocals, this.ctx).catch(() => null) : Promise.resolve(null),
      ]);
      return { instrumental, vocals };
    })();
    this.cache.set(key, p);
    this.cacheOrder = this.cacheOrder.filter((k) => k !== key).concat(key);
    p.catch(() => this.forget(key));
    this._evict();
    return p;
  }

  forget(key) {
    this.cache.delete(key);
    this.cacheOrder = this.cacheOrder.filter((k) => k !== key);
  }

  _evict() {
    while (this.cacheOrder.length > MAX_CACHED) {
      const old = this.cacheOrder.find((k) => !this.track || k !== this.track.key);
      if (!old) break;
      this.forget(old);
    }
  }

  /** Seleciona a música carregada como atual (para qualquer reprodução anterior). */
  async setTrack(key, media) {
    const buffers = await this.load(key, media);
    this.stop();
    this.track = { key, ...buffers };
    this.offsetSec = 0;
    this._evict();
    return this.track;
  }

  /** Esquece a música atual (fim da fila). */
  clearTrack() {
    this.stop();
    this.track = null;
    this.offsetSec = 0;
  }

  get duration() {
    return this.track ? this.track.instrumental.duration : 0;
  }

  /** Posição atual em segundos, derivada do relógio do AudioContext. */
  get position() {
    if (!this.track) return 0;
    if (!this.playing) return this.offsetSec;
    const pos = this.offsetSec + Math.max(0, this.ctx.currentTime - this.startedAt);
    return Math.min(pos, this.duration);
  }

  /** Toca a partir de `fromSec` (ou da posição atual). */
  play(fromSec = this.position) {
    if (!this.track || !this.unlocked) return false;
    this._stopSources();
    const offset = Math.max(0, Math.min(fromSec, Math.max(0, this.duration - 0.05)));
    const when = this.ctx.currentTime + START_DELAY_S;
    const inst = this.ctx.createBufferSource();
    inst.buffer = this.track.instrumental;
    inst.connect(this.instGain);
    let voc = null;
    if (this.track.vocals) {
      voc = this.ctx.createBufferSource();
      voc.buffer = this.track.vocals;
      voc.connect(this.vocGain);
    }
    const sources = { inst, voc };
    inst.onended = () => {
      // `ended` também dispara em stop(); só conta se ainda é a fonte atual.
      if (this.sources !== sources) return;
      this.sources = null;
      this.playing = false;
      this.offsetSec = this.duration;
      this.onEnded();
    };
    inst.start(when, offset);
    if (voc) voc.start(when, Math.min(offset, voc.buffer.duration));
    this.sources = sources;
    this.offsetSec = offset;
    this.startedAt = when;
    this.playing = true;
    return true;
  }

  pause() {
    if (!this.playing) return;
    const pos = this.position;
    this._stopSources();
    this.playing = false;
    this.offsetSec = pos;
  }

  /** Seek absoluto em segundos; mantém tocando se estava tocando. */
  seek(sec) {
    const target = Math.max(0, Math.min(sec, this.duration));
    if (this.playing) this.play(target);
    else this.offsetSec = target;
  }

  stop() {
    this._stopSources();
    this.playing = false;
  }

  _stopSources() {
    const s = this.sources;
    this.sources = null;
    if (!s) return;
    for (const src of [s.inst, s.voc]) {
      if (!src) continue;
      try {
        src.stop();
      } catch {
        /* já parada */
      }
      src.disconnect();
    }
  }

  /** Volume 0..1 (curva perceptual: ganho = v²). */
  setVolume(v) {
    this.volume = Math.max(0, Math.min(1, Number(v) || 0));
    if (this.ctx) {
      this.master.gain.setTargetAtTime(this.volume * this.volume, this.ctx.currentTime, GAIN_TIME_CONSTANT);
    }
  }

  /** Voz guia 0..1. */
  setGuide(v) {
    this.guide = Math.max(0, Math.min(1, Number(v) || 0));
    if (this.ctx) this.vocGain.gain.setTargetAtTime(this.guide, this.ctx.currentTime, GAIN_TIME_CONSTANT);
  }
}
