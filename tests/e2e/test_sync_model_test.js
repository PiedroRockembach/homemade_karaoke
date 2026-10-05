// Testes do modelo puro do editor de sincronia (US-09, sem DOM). Rodar: .venv/bin/deno test tests/e2e/test_sync_model_test.js
// Executado também por tests/e2e/test_stage_js.py (make test). Dono: qa-engineer (T-007, ciclo 20261004-1742).
import * as M from "../../karaoke/web/js/control/sync-model.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;
function ok(cond, msg) {
  if (!cond) throw new Error(msg || "condição falsa");
}

const DOC = {
  version: 1,
  song_id: "x",
  source: "lrclib",
  timing: "line",
  lines: [
    { i: 0, start_ms: 1000, end_ms: 2500, text: "Linha um", words: null },
    { i: 1, start_ms: 3000, end_ms: 4500, text: "Linha dois", words: [] },
    { i: 2, start_ms: 5000, end_ms: 6500, text: "Linha três", words: null, interpolated: true },
    { i: 3, start_ms: 7000, text: "Linha quatro" },
  ],
};
const starts = (lines) => lines.map((l) => l.start);
const fresh = () => M.linesFromDoc(DOC);

// ------------------------------------------------------------------ construção

Deno.test("linesFromDoc: start/orig/end/words/interpolated", () => {
  const ls = fresh();
  eq(starts(ls), [1000, 3000, 5000, 7000]);
  eq(ls.map((l) => l.orig), [1000, 3000, 5000, 7000]);
  eq(ls[3].end, null, "end ausente vira null");
  eq(ls[1].words, null, "words vazio vira null");
  eq(ls.map((l) => l.interpolated), [false, false, true, false]);
  eq(M.linesFromDoc(null), []);
  eq(M.linesFromDoc({}), []);
});

Deno.test("linesFromText: uma linha por verso, sem tempo, ignora vazias, corta em 500", () => {
  const ls = M.linesFromText("  a  \r\n\nb\n" + "x".repeat(600));
  eq(ls.map((l) => l.text.length), [1, 1, 500]);
  ok(ls.every((l) => l.start === null && l.orig === null && !l.interpolated));
  eq(M.linesFromText(""), []);
  eq(M.linesFromText(null), []);
});

// ------------------------------------------------------------------ flag de alterada

Deno.test("changeCount/timingChanged: linhas alteradas + offset", () => {
  const ls = fresh();
  eq(M.changeCount(ls, 0, 0), 0);
  ok(!M.timingChanged(ls));
  M.shiftLine(ls, 1, 10);
  eq(M.changeCount(ls, 0, 0), 1);
  ok(M.timingChanged(ls));
  eq(M.changeCount(ls, 250, 0), 2, "offset conta 1");
  M.shiftLine(ls, 1, -10);
  eq(M.changeCount(ls, 250, 0), 1, "voltar ao valor salvo limpa a flag da linha");
  ok(!M.timingChanged(ls), "só offset → PATCH, sem PUT");
});

// ------------------------------------------------------------------ deslocar linha / ajuste fino

Deno.test("shiftLine sem ripple: só a linha", () => {
  const ls = fresh();
  M.shiftLine(ls, 1, 100);
  eq(starts(ls), [1000, 3100, 5000, 7000]);
  M.shiftLine(ls, 1, -10);
  eq(starts(ls), [1000, 3090, 5000, 7000]);
});

Deno.test("shiftLine com ripple: a linha e as seguintes até a próxima marcada", () => {
  const ls = fresh();
  M.shiftLine(ls, 1, 100, { ripple: true });
  eq(starts(ls), [1000, 3100, 5100, 7100]);
  const ls2 = fresh();
  M.shiftLine(ls2, 0, -100, { ripple: true, marked: [false, false, true, false] });
  eq(starts(ls2), [900, 2900, 5000, 7000], "linha 2 marcada é âncora");
});

Deno.test("shiftLine: limite em 0, arredonda, delta 0/índice inválido/linha sem tempo não fazem nada", () => {
  const ls = fresh();
  M.shiftLine(ls, 0, -5000, { ripple: true });
  eq(starts(ls), [0, 0, 0, 2000], "nunca negativo");
  const ls2 = fresh();
  M.shiftLine(ls2, 0, 10.6);
  eq(ls2[0].start, 1011);
  M.shiftLine(ls2, 0, 0);
  M.shiftLine(ls2, 99, 100);
  M.shiftLine(ls2, -1, 100);
  eq(starts(ls2), [1011, 3000, 5000, 7000]);
  const t = M.linesFromText("a\nb");
  M.shiftLine(t, 0, 100, { ripple: true });
  eq(starts(t), [null, null]);
  const mixed = fresh();
  mixed[2].start = null;
  M.shiftLine(mixed, 1, 50, { ripple: true });
  eq(starts(mixed), [1000, 3050, null, 7050], "ripple pula linha sem tempo");
});

Deno.test("shiftLine na última linha (ripple sem seguintes)", () => {
  const ls = fresh();
  M.shiftLine(ls, 3, 200, { ripple: true });
  eq(starts(ls), [1000, 3000, 5000, 7200]);
});

// ------------------------------------------------------------------ tocar e marcar

Deno.test("markLine sequencial com ripple: seguintes andam o mesmo tanto até a marcada", () => {
  const ls = fresh();
  const marked = ls.map(() => false);
  M.markLine(ls, marked, 0, 1200, { ripple: true });
  eq(starts(ls), [1200, 3200, 5200, 7200]);
  eq(marked, [true, false, false, false]);
  M.markLine(ls, marked, 1, 3100.4, { ripple: true });
  eq(starts(ls), [1200, 3100, 5100, 7100]);
  M.markLine(ls, marked, 2, 5000, { ripple: true });
  M.markLine(ls, marked, 3, 6900, { ripple: true });
  eq(starts(ls), [1200, 3100, 5000, 6900]);
  eq(marked, [true, true, true, true]);
});

Deno.test("markLine: âncora marcada à frente, sem ripple, limite 0 e linha sem tempo", () => {
  const ls = fresh();
  const marked = [false, false, true, false];
  M.markLine(ls, marked, 0, 1500, { ripple: true });
  eq(starts(ls), [1500, 3500, 5000, 7000], "para na linha já marcada");
  const ls2 = fresh();
  const m2 = ls2.map(() => false);
  M.markLine(ls2, m2, 1, 3500);
  eq(starts(ls2), [1000, 3500, 5000, 7000], "sem ripple só a linha");
  M.markLine(ls2, m2, 0, -300);
  eq(ls2[0].start, 0);
  const t = M.linesFromText("a\nb\nc");
  const mt = t.map(() => false);
  M.markLine(t, mt, 0, 800, { ripple: true });
  eq(starts(t), [800, null, null], "linha sem tempo não gera delta");
  M.markLine(t, mt, 9, 100);
  eq(mt, [true, false, false], "índice inválido ignorado");
});

// ------------------------------------------------------------------ validação

Deno.test("outOfOrder: tempos que não crescem (igual ou menor que a anterior)", () => {
  const ls = fresh();
  eq(M.outOfOrder(ls), []);
  ls[2].start = 3000; // igual à anterior
  ls[3].start = 2000; // menor
  eq(M.outOfOrder(ls), [2, 3]);
  const gap = fresh();
  gap[1].start = null;
  gap[2].start = 900;
  eq(M.outOfOrder(gap), [2], "compara com a anterior que tem tempo");
});

Deno.test("validateLines: empty, untimed, not_sorted, after_end, ok", () => {
  eq(M.validateLines([], 10000), { line: 0, reason: "empty", count: 0 });
  const t = M.linesFromText("a\nb\nc");
  t[0].start = 100;
  eq(M.validateLines(t, 10000), { line: 1, reason: "untimed", count: 2 });
  const ls = fresh();
  ls[3].start = 4000;
  eq(M.validateLines(ls, 10000), { line: 3, reason: "not_sorted", count: 1 });
  const late = fresh();
  eq(M.validateLines(late, 7000), { line: 3, reason: "after_end", count: 1 }, "≥ duração");
  eq(M.validateLines(late, 7001), null);
  eq(M.validateLines(late, 0), null, "sem duração não checa o fim");
});

// ------------------------------------------------------------------ serialização

Deno.test("timingPayload: texto + start_ms arredondado, candidate_id opcional", () => {
  const ls = fresh();
  ls[0].start = 1000.6;
  eq(M.timingPayload(ls.slice(0, 2), "c_1"), {
    lines: [{ text: "Linha um", start_ms: 1001 }, { text: "Linha dois", start_ms: 3000 }],
    candidate_id: "c_1",
  });
  ok(!("candidate_id" in M.timingPayload(ls, null)));
  eq(M.timingPayload([], null), { lines: [] });
});

// ------------------------------------------------------------------ linha atual / prévia

Deno.test("lineAt: última linha com start ≤ t; -1 antes da primeira; ignora sem tempo", () => {
  const ls = fresh();
  eq(M.lineAt(ls, 0), -1);
  eq(M.lineAt(ls, 1000), 0);
  eq(M.lineAt(ls, 4999), 1);
  eq(M.lineAt(ls, 99999), 3);
  ls[1].start = null;
  eq(M.lineAt(ls, 4000), 0);
  eq(M.lineAt([], 100), -1);
});

Deno.test("lineEnd: fim original deslocado, limitado pela próxima e pela duração; estimado sem end", () => {
  const ls = fresh();
  eq(M.lineEnd(ls, 0), 2500);
  M.shiftLine(ls, 0, 100);
  eq(M.lineEnd(ls, 0), 2600, "end acompanha o deslocamento");
  M.shiftLine(ls, 0, 600);
  eq(M.lineEnd(ls, 0), 3000, "limitado pela próxima linha");
  eq(M.lineEnd(ls, 3), 7000 + 1500, "sem end: estimativa mínima de 1,5 s");
  eq(M.lineEnd(ls, 3, 7500), 7500, "limitado pela duração");
  eq(M.lineEnd(M.linesFromText("a"), 0), null);
  eq(M.lineEnd(ls, 99), null);
});

Deno.test("wordProgress: proporcional sem words; usa words deslocadas", () => {
  const ls = fresh();
  eq(M.wordProgress(ls, 0, 500), [0, 0]);
  eq(M.wordProgress(ls, 0, 2500), [1, 1]);
  const mid = M.wordProgress(ls, 0, 1750);
  ok(mid[0] > 0 && mid[1] < 1, JSON.stringify(mid));
  const w = M.linesFromDoc({
    lines: [{
      start_ms: 1000,
      end_ms: 2000,
      text: "a b",
      words: [{ text: "a", start_ms: 1000, end_ms: 1500 }, { text: "b", start_ms: 1500, end_ms: 2000 }],
    }],
  });
  const p = M.wordProgress(w, 0, 1250);
  ok(near(p[0], 0.5) && p[1] === 0, JSON.stringify(p));
  M.shiftLine(w, 0, 500);
  const q = M.wordProgress(w, 0, 1750);
  ok(near(q[0], 0.5) && q[1] === 0, "palavras andam com a linha " + JSON.stringify(q));
  eq(M.wordProgress(M.linesFromText("x y"), 0, 100), [0, 0]);
});

// ------------------------------------------------------------------ desfazer / refazer / offset

Deno.test("record/undo/redo/restore com linhas, marcadas e offset (aplicar sugestão)", () => {
  const ls = fresh();
  const marked = ls.map(() => false);
  let offset = 0;
  const h = M.createHistory();
  ok(M.record(h, M.snapshot(ls, offset, marked), "fine:1", 1000));
  M.shiftLine(ls, 1, 100, { ripple: true });
  ok(M.record(h, M.snapshot(ls, offset, marked), null, 2000));
  offset = 350; // "Aplicar" a sugestão de Detectar automaticamente
  eq(M.changeCount(ls, offset, 0), 4);
  let s = M.undo(h, M.snapshot(ls, offset, marked));
  offset = M.restore(ls, marked, s);
  eq(offset, 0, "desfaz o offset");
  eq(starts(ls), [1000, 3100, 5100, 7100]);
  s = M.undo(h, M.snapshot(ls, offset, marked));
  offset = M.restore(ls, marked, s);
  eq(starts(ls), [1000, 3000, 5000, 7000]);
  eq(M.changeCount(ls, offset, 0), 0);
  eq(M.undo(h, M.snapshot(ls, offset, marked)), null, "pilha vazia");
  s = M.redo(h, M.snapshot(ls, offset, marked));
  offset = M.restore(ls, marked, s);
  eq(starts(ls), [1000, 3100, 5100, 7100]);
  s = M.redo(h, M.snapshot(ls, offset, marked));
  offset = M.restore(ls, marked, s);
  eq(offset, 350);
  eq(M.redo(h, M.snapshot(ls, offset, marked)), null);
});

Deno.test("record: mesma chave em < 600 ms vira um passo; chave diferente/tempo maior/null não agrupam", () => {
  const h = M.createHistory();
  const snap = { starts: [], offset: 0, marked: [] };
  ok(M.record(h, snap, "k", 0));
  ok(!M.record(h, snap, "k", 599));
  ok(!M.record(h, snap, "k", 1100), "a janela conta da última mudança");
  ok(M.record(h, snap, "k", 1700));
  ok(M.record(h, snap, "j", 1701));
  ok(M.record(h, snap, null, 1702));
  ok(M.record(h, snap, null, 1703));
  eq(h.undo.length, 5);
  M.undo(h, snap);
  ok(M.record(h, snap, "j", 1704), "depois de desfazer não agrupa");
  eq(h.redo.length, 0, "mudança nova limpa o refazer");
});

Deno.test("record: pilha limitada a 500 passos", () => {
  const h = M.createHistory();
  for (let i = 0; i < 510; i++) M.record(h, { starts: [i], offset: 0, marked: [] }, null, i);
  eq(h.undo.length, 500);
  eq(h.undo[0].starts, [10]);
});

Deno.test("desfazer marcação restaura as marcadas (Tocar e marcar)", () => {
  const ls = fresh();
  const marked = ls.map(() => false);
  const h = M.createHistory();
  M.record(h, M.snapshot(ls, 0, marked), "mark", 0);
  M.markLine(ls, marked, 0, 1300, { ripple: true });
  const s = M.undo(h, M.snapshot(ls, 0, marked));
  M.restore(ls, marked, s);
  eq(marked, [false, false, false, false]);
  eq(starts(ls), [1000, 3000, 5000, 7000]);
  const snap = M.snapshot(ls, 0, marked);
  marked[0] = true;
  eq(snap.marked[0], false, "snapshot copia as marcadas");
});
