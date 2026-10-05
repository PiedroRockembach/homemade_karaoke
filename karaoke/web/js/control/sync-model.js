/**
 * Modelo puro do editor de sincronia (US-09): linhas com tempo editável, "Mover as seguintes", validação antes de
 * salvar, linha atual por busca binária e pilha de desfazer/refazer com agrupamento. Sem DOM.
 *
 * Convenções (lyrics-format §4): `start` é o tempo da letra **sem** o offset da música; o que se ouve/vê é
 * `start + offset` (offset positivo = letra mais tarde).
 *
 * Dono: frontend-engineer (T-003).
 */

export const GROUP_MS = 600;

/**
 * @typedef {{text: string, start: number|null, orig: number|null, end: number|null, words: object[]|null,
 *            interpolated: boolean}} SyncLine
 */

/** Linhas do editor a partir de um documento Lyrics. */
export function linesFromDoc(doc) {
  return ((doc && doc.lines) || []).map((l) => ({
    text: l.text,
    start: l.start_ms,
    orig: l.start_ms,
    end: l.end_ms ?? null,
    words: Array.isArray(l.words) && l.words.length ? l.words : null,
    interpolated: Boolean(l.interpolated),
  }));
}

/** Linhas sem tempo a partir de texto colado (uma por verso). */
export function linesFromText(text) {
  return String(text || "").split(/\r?\n/).map((s) => s.trim()).filter(Boolean).map((t) => ({
    text: t.slice(0, 500),
    start: null,
    orig: null,
    end: null,
    words: null,
    interpolated: false,
  }));
}

/** Nº de alterações: linhas com tempo diferente do salvo + 1 se o offset mudou. */
export function changeCount(lines, offset, savedOffset) {
  let n = 0;
  for (const l of lines) if (l.start !== l.orig) n++;
  if (offset !== savedOffset) n++;
  return n;
}

/** true se algum tempo de linha mudou (precisa de PUT manual-timing). */
export function timingChanged(lines) {
  return lines.some((l) => l.start !== l.orig);
}

/**
 * Desloca a linha `i` em `delta` ms. Com `ripple`, desloca também as seguintes até a próxima linha marcada nesta
 * sessão (ela serve de âncora). Linhas sem tempo não se movem. Muta `lines`.
 */
export function shiftLine(lines, i, delta, { ripple = false, marked = [] } = {}) {
  const l = lines[i];
  if (!l || l.start == null || !delta) return;
  l.start = Math.max(0, Math.round(l.start + delta));
  if (!ripple) return;
  for (let j = i + 1; j < lines.length; j++) {
    if (marked[j]) break;
    if (lines[j].start != null) lines[j].start = Math.max(0, Math.round(lines[j].start + delta));
  }
}

/**
 * Grava o tempo da linha `i` (tocar e marcar). Com `ripple`, as seguintes não marcadas andam o mesmo tanto que a
 * linha andou (se ela já tinha tempo). Muta `lines` e `marked`.
 */
export function markLine(lines, marked, i, start, { ripple = false } = {}) {
  const l = lines[i];
  if (!l) return;
  const t = Math.max(0, Math.round(start));
  const delta = l.start == null ? 0 : t - l.start;
  l.start = t;
  marked[i] = true;
  if (ripple && delta) {
    for (let j = i + 1; j < lines.length; j++) {
      if (marked[j]) break;
      if (lines[j].start != null) lines[j].start = Math.max(0, Math.round(lines[j].start + delta));
    }
  }
}

/** Índices das linhas fora de ordem (começam em ou antes da anterior com tempo). */
export function outOfOrder(lines) {
  const bad = [];
  let prev = null;
  lines.forEach((l, i) => {
    if (l.start == null) return;
    if (prev != null && l.start <= prev) bad.push(i);
    prev = l.start;
  });
  return bad;
}

/**
 * Valida antes de salvar. Retorna null se ok, ou {line, reason, count}:
 * `untimed` (linhas sem tempo), `not_sorted`, `after_end` (≥ duração), `empty`.
 */
export function validateLines(lines, durationMs) {
  if (!lines.length) return { line: 0, reason: "empty", count: 0 };
  const untimed = lines.map((l, i) => (l.start == null ? i : -1)).filter((i) => i >= 0);
  if (untimed.length) return { line: untimed[0], reason: "untimed", count: untimed.length };
  const bad = outOfOrder(lines);
  if (bad.length) return { line: bad[0], reason: "not_sorted", count: bad.length };
  if (durationMs) {
    const late = lines.findIndex((l) => l.start >= durationMs);
    if (late >= 0) return { line: late, reason: "after_end", count: 1 };
  }
  return null;
}

/** Payload de `PUT …/lyrics/manual-timing` (o backend completa end_ms/palavras a partir da base). */
export function timingPayload(lines, candidateId) {
  const body = { lines: lines.map((l) => ({ text: l.text, start_ms: Math.round(l.start) })) };
  if (candidateId) body.candidate_id = candidateId;
  return body;
}

/** Índice da última linha com `start ≤ t` (ignora linhas sem tempo), ou -1. Linear se fora de ordem. */
export function lineAt(lines, t) {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].start;
    if (s == null) continue;
    if (s <= t) idx = i;
    else break;
  }
  return idx;
}

/** Fim estimado de uma linha (para a prévia): fim original deslocado, limitado pela próxima linha. */
export function lineEnd(lines, i, durationMs = 0) {
  const l = lines[i];
  if (!l || l.start == null) return null;
  let next = null;
  for (let j = i + 1; j < lines.length; j++) {
    if (lines[j].start != null) {
      next = lines[j].start;
      break;
    }
  }
  let end;
  if (l.end != null && l.orig != null) end = l.end + (l.start - l.orig);
  else {
    const words = l.text.split(/\s+/).length;
    end = l.start + Math.max(1500, Math.min(8000, words * 450 + 600));
  }
  if (next != null) end = Math.min(end, next);
  if (durationMs) end = Math.min(end, durationMs);
  return Math.max(end, l.start + 1);
}

/** Progresso 0..1 de cada palavra da linha `i` em `t` (prévia estilo Palco). */
export function wordProgress(lines, i, t, durationMs = 0) {
  const l = lines[i];
  const end = lineEnd(lines, i, durationMs);
  const texts = l.text.split(/\s+/);
  if (end == null) return texts.map(() => 0);
  const shift = l.orig != null ? l.start - l.orig : 0;
  if (l.words && l.words.length === texts.length) {
    return l.words.map((w) => {
      const ws = w.start_ms + shift;
      const we = Math.min(w.end_ms + shift, end);
      if (t <= ws) return 0;
      if (t >= we) return 1;
      return (t - ws) / Math.max(1, we - ws);
    });
  }
  const total = texts.reduce((a, w) => a + w.length + 1, 0);
  let acc = 0;
  return texts.map((w) => {
    const ws = l.start + ((end - l.start) * acc) / total;
    acc += w.length + 1;
    const we = l.start + ((end - l.start) * acc) / total;
    if (t <= ws) return 0;
    if (t >= we) return 1;
    return (t - ws) / Math.max(1, we - ws);
  });
}

/** Pilha de desfazer/refazer de instantâneos `{starts, offset, marked}`. */
export function createHistory() {
  return { undo: [], redo: [], lastKey: null, lastAt: 0 };
}

export function snapshot(lines, offset, marked) {
  return { starts: lines.map((l) => l.start), offset, marked: [...marked] };
}

/**
 * Registra o estado ANTES de uma mudança. Mudanças seguidas com a mesma `key` em < 600 ms viram um passo só.
 * @returns {boolean} true se um passo novo foi empilhado
 */
export function record(hist, snap, key = null, now = Date.now()) {
  const grouped = key && hist.lastKey === key && now - hist.lastAt < GROUP_MS;
  hist.lastKey = key;
  hist.lastAt = now;
  if (grouped) return false;
  hist.undo.push(snap);
  if (hist.undo.length > 500) hist.undo.shift();
  hist.redo.length = 0;
  return true;
}

/** Desfaz: devolve o instantâneo a aplicar (ou null) e guarda `current` no refazer. */
export function undo(hist, current) {
  const snap = hist.undo.pop();
  if (!snap) return null;
  hist.redo.push(current);
  hist.lastKey = null;
  return snap;
}

export function redo(hist, current) {
  const snap = hist.redo.pop();
  if (!snap) return null;
  hist.undo.push(current);
  hist.lastKey = null;
  return snap;
}

/** Aplica um instantâneo em `lines`/`marked`; devolve o offset. */
export function restore(lines, marked, snap) {
  snap.starts.forEach((s, i) => {
    if (lines[i]) lines[i].start = s;
  });
  marked.length = 0;
  marked.push(...snap.marked);
  return snap.offset;
}
