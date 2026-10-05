/**
 * Palco: lógica pura de tempo da letra (sem DOM) — lyrics-format.md §4–5 e DESIGN §6.
 *
 * - `prepareLyrics(doc)` normaliza o documento: toda linha ganha `words` com início/fim.
 *   · Whisper / Enhanced LRC: usa os tempos por palavra do documento.
 *   · LRC comum (só tempo de linha): fim do preenchimento = min(end_ms, próxima − 200 ms,
 *     início + 450 ms × palavras + 800 ms) e o intervalo é dividido entre as palavras
 *     proporcionalmente ao nº de caracteres (incluindo o espaço) → preenchimento contínuo.
 * - `computeView(prepared, t)` diz o que mostrar no instante `t` (ms de letra, já com latência e offset).
 *
 * Testado em tests/e2e/test_stage_lyrics_test.js (deno test). Dono: frontend-engineer (T-007).
 */

export const GAP_MS = 5000; // intervalo instrumental que mostra o indicador de espera
export const COUNTDOWN_MS = 3000; // últimos 3 s: pontos acendem (3-2-1)

function estimatedWords(line, nextStart) {
  const tokens = line.text.split(/\s+/).filter(Boolean);
  const n = Math.max(1, tokens.length);
  let fillEnd = Math.min(line.end_ms, line.start_ms + 450 * n + 800);
  if (Number.isFinite(nextStart)) fillEnd = Math.min(fillEnd, nextStart - 200);
  fillEnd = Math.max(fillEnd, line.start_ms + 300);
  const dur = fillEnd - line.start_ms;
  const total = tokens.reduce((s, w) => s + w.length + 1, 0);
  let acc = line.start_ms;
  return tokens.map((w) => {
    const d = (dur * (w.length + 1)) / total;
    const word = { text: w, start_ms: acc, end_ms: acc + d };
    acc += d;
    return word;
  });
}

/**
 * @param {object|null} doc documento Lyrics (lyrics-format.md §1)
 * @returns {{lines: Array<{i:number,start_ms:number,end_ms:number,text:string,words:Array}>, timing: string}}
 */
export function prepareLyrics(doc) {
  const raw = (doc && Array.isArray(doc.lines)) ? doc.lines : [];
  const src = raw
    .filter((l) => l && typeof l.text === "string" && l.text.trim() && Number.isFinite(l.start_ms))
    .slice()
    .sort((a, b) => a.start_ms - b.start_ms);
  const lines = src.map((l, i) => {
    const next = src[i + 1];
    const end = Number.isFinite(l.end_ms) && l.end_ms > l.start_ms ? l.end_ms : l.start_ms + 3000;
    const line = {
      i,
      start_ms: l.start_ms,
      end_ms: next ? Math.min(end, next.start_ms) : end,
      text: l.text.trim(),
    };
    if (line.end_ms <= line.start_ms) line.end_ms = line.start_ms + 1;
    line.words = Array.isArray(l.words) && l.words.length
      ? l.words.map((w) => ({
        text: w.text,
        start_ms: w.start_ms,
        end_ms: Math.max(w.end_ms, w.start_ms + 1),
      }))
      : estimatedWords(line, next ? next.start_ms : NaN);
    return line;
  });
  return { lines, timing: lines.length ? (doc.timing || "line") : "none" };
}

/** Progresso 0..1 de uma palavra no instante t. */
export function wordProgress(word, t) {
  if (t <= word.start_ms) return 0;
  if (t >= word.end_ms) return 1;
  return (t - word.start_ms) / (word.end_ms - word.start_ms);
}

/** Índice da primeira linha com t < end_ms (busca binária; linhas não se sobrepõem). n se nenhuma. */
export function lineIndexAt(lines, t) {
  let lo = 0;
  let hi = lines.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].end_ms <= t) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/**
 * O que mostrar no instante t (ms).
 * @returns {{mode: "none"|"lines"|"wait"|"end", idx: number, remainingMs?: number, intro?: boolean,
 *            dotsOn?: number}}
 *   - lines: `idx` = linha atual (grande), `idx+1` = próxima.
 *   - wait: intervalo > 5 s antes de `idx` (intro se idx = 0); `remainingMs` até a linha voltar;
 *           `dotsOn` = 0..3 pontos acesos (contagem final).
 *   - end: depois da última linha.
 */
export function computeView(prepared, t) {
  const { lines } = prepared;
  if (!lines.length) return { mode: "none", idx: -1 };
  const idx = lineIndexAt(lines, t);
  if (idx >= lines.length) return { mode: "end", idx };
  const line = lines[idx];
  if (t < line.start_ms) {
    const prevEnd = idx > 0 ? lines[idx - 1].end_ms : 0;
    if (line.start_ms - prevEnd > GAP_MS) {
      const remainingMs = line.start_ms - t;
      const dotsOn = remainingMs <= COUNTDOWN_MS ? Math.min(3, 3 - Math.ceil(remainingMs / 1000) + 1) : 0;
      return { mode: "wait", idx, remainingMs, intro: idx === 0, dotsOn };
    }
  }
  return { mode: "lines", idx };
}

/** Legenda do indicador de espera (DESIGN §5.3). */
export function waitLabel(view) {
  const n = Math.max(1, Math.ceil(view.remainingMs / 1000));
  if (view.intro) return view.remainingMs > COUNTDOWN_MS ? "Prepare-se…" : `Começa em ${n}`;
  return view.remainingMs > COUNTDOWN_MS ? `Solo instrumental · volta em ${n} s` : `Volta em ${n}`;
}
