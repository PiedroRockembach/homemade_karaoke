// Testes da lógica de tempo da letra do Palco (sem DOM). Rodar: .venv/bin/deno test tests/e2e/test_stage_lyrics_test.js
// Executado também por tests/e2e/test_stage_js.py (make test). Dono: frontend-engineer (T-007).
import {
  computeView,
  lineIndexAt,
  prepareLyrics,
  waitLabel,
  wordProgress,
} from "../../karaoke/web/js/stage/lyrics-timing.js";
import { fmtOffset, fmtTime, jobStageLabel } from "../../karaoke/web/js/shared/format.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}
const near = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;

// Exemplo do contrato lyrics-format.md §6 (LRC comum, linha 2 termina em 18200, intervalo até 45100).
const LRC_DOC = {
  version: 1,
  song_id: "fJ9rUzIMcZQ",
  source: "lrclib",
  timing: "line",
  lines: [
    { i: 0, start_ms: 12340, end_ms: 15800, text: "Is this the real life?", words: null },
    { i: 1, start_ms: 15800, end_ms: 18200, text: "Is this just fantasy?", words: null },
    { i: 2, start_ms: 45100, end_ms: 47500, text: "Mama, just killed a man", words: null },
  ],
};

const WORD_DOC = {
  version: 1,
  song_id: "x",
  source: "whisper",
  timing: "word",
  lines: [{
    i: 0,
    start_ms: 15800,
    end_ms: 18900,
    text: "Is this just fantasy?",
    words: [
      { text: "Is", start_ms: 15800, end_ms: 16050 },
      { text: "this", start_ms: 16050, end_ms: 16400 },
      { text: "just", start_ms: 16400, end_ms: 16900 },
      { text: "fantasy?", start_ms: 16900, end_ms: 18900 },
    ],
  }],
};

Deno.test("sem letra → mode none", () => {
  eq(computeView(prepareLyrics(null), 1000).mode, "none");
  eq(computeView(prepareLyrics({ timing: "none", lines: [] }), 1000).mode, "none");
});

Deno.test("LRC comum: palavras estimadas cobrem a linha, proporcionais aos caracteres", () => {
  const { lines } = prepareLyrics(LRC_DOC);
  const l0 = lines[0];
  eq(l0.words.map((w) => w.text), ["Is", "this", "the", "real", "life?"]);
  eq(l0.words[0].start_ms, 12340, "1ª palavra começa no início da linha");
  // fim = min(end 15800, próxima−200 = 15600, início + 450×5 + 800 = 15390) = 15390
  if (!near(l0.words.at(-1).end_ms, 15390)) throw new Error("fim estimado " + l0.words.at(-1).end_ms);
  for (let k = 1; k < l0.words.length; k++) {
    if (!near(l0.words[k].start_ms, l0.words[k - 1].end_ms)) throw new Error("palavras contíguas");
  }
  // "this" (5 chars c/ espaço) dura 5/3 de "Is" (3 chars)
  const d = (w) => w.end_ms - w.start_ms;
  if (!near(d(l0.words[1]) / d(l0.words[0]), 5 / 3, 1e-9)) throw new Error("proporção por caracteres");
});

Deno.test("Whisper: usa os tempos por palavra do documento", () => {
  const { lines } = prepareLyrics(WORD_DOC);
  eq(lines[0].words[2], { text: "just", start_ms: 16400, end_ms: 16900 });
  eq(wordProgress(lines[0].words[2], 16650), 0.5);
  eq(wordProgress(lines[0].words[2], 16000), 0);
  eq(wordProgress(lines[0].words[2], 17000), 1);
});

Deno.test("linha atual = primeira com t < fim (busca binária)", () => {
  const { lines } = prepareLyrics(LRC_DOC);
  eq(lineIndexAt(lines, 0), 0);
  eq(lineIndexAt(lines, 15799), 0);
  eq(lineIndexAt(lines, 15800), 1);
  eq(lineIndexAt(lines, 20000), 2);
  eq(lineIndexAt(lines, 99999), 3);
});

Deno.test("intro > 5 s e intervalo instrumental > 5 s mostram espera com contagem", () => {
  const prepared = prepareLyrics(LRC_DOC);
  const intro = computeView(prepared, 2000); // 1ª linha em 12,34 s
  eq([intro.mode, intro.idx, intro.intro, intro.dotsOn], ["wait", 0, true, 0]);
  eq(waitLabel(intro), "Prepare-se…");
  const introEnd = computeView(prepared, 10340); // faltam 2 s
  eq([introEnd.dotsOn, waitLabel(introEnd)], [2, "Começa em 2"]);

  const gap = computeView(prepared, 30000); // entre 18,2 s e 45,1 s
  eq([gap.mode, gap.idx, gap.intro], ["wait", 2, false]);
  eq(waitLabel(gap), "Solo instrumental · volta em 16 s");
  eq(computeView(prepared, 44600).dotsOn, 3);
  eq(waitLabel(computeView(prepared, 44600)), "Volta em 1");
});

Deno.test("vão curto (< 5 s) não vira espera; depois da última linha → end", () => {
  const doc = {
    timing: "line",
    lines: [
      { start_ms: 1000, end_ms: 2000, text: "a b" },
      { start_ms: 4000, end_ms: 5000, text: "c d" },
    ],
  };
  const prepared = prepareLyrics(doc);
  eq(computeView(prepared, 3000), { mode: "lines", idx: 1 });
  eq(computeView(prepared, 500).mode, "lines", "intro de 1 s");
  eq(computeView(prepared, 6000).mode, "end");
});

Deno.test("linhas sobrepostas ou fora de ordem são normalizadas", () => {
  const prepared = prepareLyrics({
    timing: "line",
    lines: [
      { start_ms: 5000, end_ms: 9000, text: "segunda" },
      { start_ms: 1000, end_ms: 7000, text: "primeira" },
      { start_ms: 8000, end_ms: 8000, text: "  " },
    ],
  });
  eq(prepared.lines.map((l) => [l.text, l.start_ms, l.end_ms]), [["primeira", 1000, 5000], [
    "segunda",
    5000,
    9000,
  ]]);
});

Deno.test("format: tempo, offset e rótulos", () => {
  eq(fmtTime(63250), "1:03");
  eq(fmtTime(4360000), "1:12:40");
  eq(fmtTime(-5), "0:00");
  eq(fmtOffset(120), "+120 ms");
  eq(fmtOffset(-50), "−50 ms");
  eq(fmtOffset(0), "0 ms");
  eq(jobStageLabel("lyrics", "whisper"), "Transcrevendo letra");
  eq(jobStageLabel("separating"), "Separando voz");
});
