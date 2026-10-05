/**
 * Forma de onda da voz separada para o editor de sincronia (DESIGN §11.6): picos calculados no navegador
 * (`decodeAudioData` → ~50 picos/s, cache em memória por URL) e desenho em `<canvas>` com cores lidas dos tokens.
 * Sem picos (falha/demora) o desenho cai para uma barra de tempo simples com os mesmos marcadores.
 *
 * Dono: frontend-engineer (T-003).
 */

export const PEAKS_PER_S = 50;
const cache = new Map();

/**
 * Baixa e decodifica `url`, devolvendo `{peaks: Float32Array (0..1), duration: ms}`.
 * @param {string} url
 * @param {AbortSignal} [signal]
 */
export function loadPeaks(url, signal) {
  if (cache.has(url)) return cache.get(url);
  const p = (async () => {
    const res = await fetch(url, { signal });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const buf = await res.arrayBuffer();
    const Ctx = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
    const ctx = new Ctx(1, 1, 22050);
    const audio = await ctx.decodeAudioData(buf);
    const n = Math.max(1, Math.ceil(audio.duration * PEAKS_PER_S));
    const peaks = new Float32Array(n);
    const per = audio.length / n;
    const chans = [];
    for (let c = 0; c < audio.numberOfChannels; c++) chans.push(audio.getChannelData(c));
    let max = 0;
    for (let i = 0; i < n; i++) {
      const a = Math.floor(i * per);
      const b = Math.min(audio.length, Math.floor((i + 1) * per));
      let m = 0;
      for (const ch of chans) {
        for (let k = a; k < b; k += 4) {
          const v = Math.abs(ch[k]);
          if (v > m) m = v;
        }
      }
      peaks[i] = m;
      if (m > max) max = m;
    }
    if (max > 0) { for (let i = 0; i < n; i++) peaks[i] = Math.sqrt(peaks[i] / max); }
    return { peaks, duration: audio.duration * 1000 };
  })();
  cache.set(url, p);
  p.catch(() => cache.delete(url));
  return p;
}

let colors = null;
let colorsKey = "";

/** Cores dos tokens (relidas quando o tema muda). */
export function waveColors() {
  const root = document.documentElement;
  const key = (root.dataset.theme || "") + (matchMedia("(prefers-color-scheme: light)").matches ? "L" : "D");
  if (colors && key === colorsKey) return colors;
  const cs = getComputedStyle(root);
  const v = (name) => cs.getPropertyValue(name).trim();
  colors = {
    played: v("--color-info"),
    rest: v("--color-text-subtle"),
    accent: v("--color-accent"),
    now: v("--color-text"),
    marker: v("--color-text-subtle"),
    label: v("--color-text-muted"),
    track: v("--color-surface-3"),
    win: v("--color-accent-soft"),
    font: v("--font-sans") || "sans-serif",
  };
  colorsKey = key;
  return colors;
}

/** Ajusta o tamanho interno do canvas ao CSS × devicePixelRatio. Devolve o contexto 2D ou null. */
function prepare(canvas) {
  const dpr = globalThis.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(canvas.clientWidth * dpr));
  const hh = Math.max(1, Math.round(canvas.clientHeight * dpr));
  if (canvas.width !== w || canvas.height !== hh) {
    canvas.width = w;
    canvas.height = hh;
  }
  const g = canvas.getContext("2d");
  if (!g) return null;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, w, hh);
  g.scale(dpr, dpr);
  return { g, w: canvas.clientWidth, h: canvas.clientHeight };
}

/**
 * Janela de `spanMs` centrada em `t` (ms de áudio), com marcadores de início de linha.
 * @param {HTMLCanvasElement} canvas
 * @param {{peaks: Float32Array|null, t: number, durationMs: number, spanMs?: number,
 *          markers: {t: number, label: string, sel: boolean}[]}} o
 */
export function drawWindow(canvas, { peaks, t, durationMs, spanMs = 10000, markers }) {
  const c = prepare(canvas);
  if (!c) return;
  const { g, w, h } = c;
  const col = waveColors();
  const t0 = t - spanMs / 2;
  const x = (ms) => ((ms - t0) / spanMs) * w;
  const top = 16;
  const mid = top + (h - top) / 2;
  const amp = (h - top) / 2 - 2;
  if (peaks) {
    const step = 2;
    for (let px = 0; px < w; px += step) {
      const ms = t0 + (px / w) * spanMs;
      if (ms < 0 || ms > durationMs) continue;
      const i = Math.floor((ms / 1000) * PEAKS_PER_S);
      const v = peaks[i] || 0;
      const hh = Math.max(1, v * amp);
      g.fillStyle = ms <= t ? col.played : col.rest;
      g.fillRect(px, mid - hh, step - 0.5, hh * 2);
    }
  } else {
    g.fillStyle = col.track;
    g.fillRect(Math.max(0, x(0)), mid - 3, Math.min(w, x(durationMs)) - Math.max(0, x(0)), 6);
    g.fillStyle = col.played;
    g.fillRect(Math.max(0, x(0)), mid - 3, Math.max(0, w / 2 - Math.max(0, x(0))), 6);
  }
  g.font = `700 12px ${col.font}`;
  g.textBaseline = "top";
  for (const m of markers) {
    const mx = x(m.t);
    if (mx < -20 || mx > w + 20) continue;
    g.fillStyle = m.sel ? col.accent : col.marker;
    g.fillRect(Math.round(mx), top, 2, h - top);
    g.fillStyle = m.sel ? col.accent : col.label;
    g.fillText(m.label, Math.round(mx) + 4, 1);
  }
  g.fillStyle = col.now;
  g.fillRect(Math.round(w / 2) - 1, 0, 2, h);
}

/**
 * Visão geral da música inteira com o retângulo da janela atual.
 * @param {HTMLCanvasElement} canvas
 * @param {{peaks: Float32Array|null, t: number, durationMs: number, spanMs?: number}} o
 */
export function drawOverview(canvas, { peaks, t, durationMs, spanMs = 10000 }) {
  const c = prepare(canvas);
  if (!c || !durationMs) return;
  const { g, w, h } = c;
  const col = waveColors();
  const mid = h / 2;
  if (peaks) {
    for (let px = 0; px < w; px++) {
      const a = Math.floor((px / w) * peaks.length);
      const b = Math.max(a + 1, Math.floor(((px + 1) / w) * peaks.length));
      let m = 0;
      for (let i = a; i < b; i++) if (peaks[i] > m) m = peaks[i];
      const hh = Math.max(0.5, m * (h / 2 - 1));
      g.fillStyle = (px / w) * durationMs <= t ? col.played : col.rest;
      g.fillRect(px, mid - hh, 1, hh * 2);
    }
  } else {
    g.fillStyle = col.track;
    g.fillRect(0, mid - 2, w, 4);
    g.fillStyle = col.played;
    g.fillRect(0, mid - 2, (t / durationMs) * w, 4);
  }
  const wx = ((t - spanMs / 2) / durationMs) * w;
  const ww = (spanMs / durationMs) * w;
  g.fillStyle = col.win;
  g.fillRect(wx, 0, ww, h);
  g.strokeStyle = col.accent;
  g.lineWidth = 1;
  g.strokeRect(wx + 0.5, 0.5, Math.max(2, ww - 1), h - 1);
}
