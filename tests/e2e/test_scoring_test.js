// Lógica pura da pontuação/guia de notas (US-18/19; US-25..27 do ciclo 20261021-0030), sem DOM nem microfone.
// Rodar: .venv/bin/deno test tests/e2e/test_scoring_test.js. Dono: frontend-engineer (T-001, ciclo 20261021-0030).
import {
  BASE_WEIGHT,
  checkPitchHit,
  COMBO_FULL_S,
  comboWeight,
  MAX_SCORE,
  noteAt,
  pointsPerSecond,
  rankFor,
  Scorer,
} from "../../karaoke/web/js/stage/scorer.js";
import { detectPitch, freqToMidi, MIC_CONSTRAINTS } from "../../karaoke/web/js/stage/mic-pitch.js";
import {
  midiRange,
  nextOctaveShift,
  octaveShiftLabel,
  parseOctaveShift,
} from "../../karaoke/web/js/stage/pitch-roll.js";
import { FADE_MS, HOLD_MS, PitchSmoother } from "../../karaoke/web/js/stage/pitch-smooth.js";

function ok(cond, msg) {
  if (!cond) throw new Error(msg || "condição falsa");
}
const eq = (a, b, msg = "") =>
  ok(JSON.stringify(a) === JSON.stringify(b), `${msg} esperado ${b}, obtido ${a}`);

Deno.test("checkPitchHit: ±50 cents contínuos, bordas 49/51, sem equivalência de oitava", () => {
  ok(checkPitchHit(60, 60), "exata");
  ok(checkPitchHit(60, 60.49) && checkPitchHit(60, 59.51), "49 cents dentro");
  ok(checkPitchHit(60, 60.5) && checkPitchHit(60, 59.5), "50 cents (borda) dentro");
  ok(!checkPitchHit(60, 60.51) && !checkPitchHit(60, 59.49), "51 cents fora");
  ok(!checkPitchHit(60, 61) && !checkPitchHit(60, 59), "1 semitom fora (antes valia)");
  ok(!checkPitchHit(60, 72) && !checkPitchHit(60, 48), "oitava acima/abaixo não vale com deslocamento 0");
  ok(checkPitchHit(60 - 12, 48.2), "deslocamento −1: alvo 48 vale para quem canta uma oitava abaixo");
  ok(!checkPitchHit(null, 60) && !checkPitchHit(60, null) && !checkPitchHit(0, 0), "sem nota");
});

Deno.test("noteAt: busca binária pelo instante", () => {
  const notes = [{ start: 1, end: 2, midi: 60 }, { start: 3, end: 3.5, midi: 62 }, {
    start: 4,
    end: 6,
    midi: 64,
  }];
  eq(noteAt(notes, 1.5)?.midi, 60);
  eq(noteAt(notes, 3)?.midi, 62);
  eq(noteAt(notes, 5.9)?.midi, 64);
  eq(noteAt(notes, 2.5), null, "entre notas");
  eq(noteAt(notes, 0), null);
  eq(noteAt([], 1), null);
});

// Simula uma música: notas de 1 s com 0,5 s de pausa; `hitFrac` = fração inicial de cada nota cantada afinada.
function simulate(fps, { hitFrac = 1, notesN = 80 } = {}) {
  const notes = Array.from({ length: notesN }, (_, i) => ({ start: i * 1.5, end: i * 1.5 + 1, midi: 60 }));
  const sc = new Scorer({ querySelector: () => null });
  sc.reset(notes);
  const dt = 1 / fps;
  const end = notesN * 1.5;
  for (let t = 0; t < end; t += dt) {
    const n = noteAt(notes, t);
    const sung = n && t - n.start < hitFrac ? 60.3 : n ? 66 : null; // 30 cents acima (vale); 66 = fora
    sc.tick(n && n.midi, sung, dt, t);
  }
  return sc.score;
}

Deno.test("pontuação: 100% afinado ≈ 10.000 e nunca passa", () => {
  const s = simulate(60);
  ok(s >= 9800 && s <= MAX_SCORE, `perfeito deu ${s}`);
});

Deno.test("pontuação: proporcional ao tempo acertado (58% não satura)", () => {
  const s = simulate(60, { hitFrac: 0.58 });
  ok(s > 5000 && s < 6000, `58% deu ${s}`);
  const half = simulate(60, { hitFrac: 0.5 });
  ok(half > 4300 && half < 5000, `50% deu ${half}`);
});

Deno.test("pontuação: independente do FPS (30 Hz × 144 Hz)", () => {
  for (const hitFrac of [1, 0.7, 0.3]) {
    const a = simulate(30, { hitFrac });
    const b = simulate(144, { hitFrac });
    ok(Math.abs(a - b) / b < 0.01, `hit ${hitFrac}: 30 Hz=${a} × 144 Hz=${b}`);
  }
});

Deno.test("comboWeight: rampa de 0,9 a 1 em COMBO_FULL_S, integração exata", () => {
  ok(Math.abs(comboWeight(0, 0.01) - (BASE_WEIGHT + (1 - BASE_WEIGHT) * 0.005 / COMBO_FULL_S)) < 1e-9);
  eq(comboWeight(COMBO_FULL_S, 0.5), 1);
  const whole = comboWeight(0, COMBO_FULL_S);
  ok(Math.abs(whole - (BASE_WEIGHT + 1) / 2) < 1e-9, `rampa inteira ${whole}`);
  // um passo grande = soma de passos pequenos
  let area = 0;
  for (let i = 0; i < 3000; i++) area += comboWeight(i * 0.001, 0.001) * 0.001;
  ok(Math.abs(area - comboWeight(0, 3) * 3) < 1e-6, "passos somam igual");
});

Deno.test("Scorer: seek para trás não pontua de novo; pausa entre notas mantém o combo", () => {
  const notes = [{ start: 0, end: 10, midi: 60 }];
  const sc = new Scorer({ querySelector: () => null });
  sc.reset(notes);
  for (let t = 0; t < 5; t += 0.1) sc.tick(60, 60, 0.1, t);
  const once = sc.score;
  for (let t = 0; t < 5; t += 0.1) sc.tick(60, 60, 0.1, t);
  eq(sc.score, once, "repetir o trecho");
  const combo = sc.comboSec;
  sc.tick(null, null, 0.5, 6);
  eq(sc.comboSec, combo, "sem nota alvo não zera combo");
  sc.tick(60, 66, 0.1, 6.1);
  eq(sc.comboSec, 0, "errar na nota zera");
  ok(pointsPerSecond(1) === pointsPerSecond(20), "músicas curtíssimas não explodem a taxa");
});

Deno.test("rankFor: mensagens por faixa", () => {
  eq(rankFor(9500), "Lendário! Afinação Perfeita!");
  eq(rankFor(8540), "Sensacional! Que show!");
  eq(rankFor(0), "Valeu a tentativa! O importante é a festa!");
});

Deno.test("freqToMidi: contínuo (cents), A4=69, inválidos", () => {
  eq(freqToMidi(440), 69);
  ok(Math.abs(freqToMidi(261.63) - 60) < 0.001, "C4");
  ok(Math.abs(freqToMidi(440 * 2 ** (25 / 1200)) - 69.25) < 1e-9, "+25 cents não arredonda");
  eq(freqToMidi(0), null);
  eq(freqToMidi(NaN), null);
});

Deno.test("MIC_CONSTRAINTS: sem supressão de ruído nem ganho automático; com cancelamento de eco", () => {
  eq(MIC_CONSTRAINTS.audio, { echoCancellation: true, noiseSuppression: false, autoGainControl: false });
});

const SR = 48000;
function tone(freq, { amp = 0.3, harmonics = false } = {}) {
  const buf = new Float32Array(2048);
  for (let i = 0; i < buf.length; i++) {
    const ph = (2 * Math.PI * freq * i) / SR;
    buf[i] = amp * (Math.sin(ph) + (harmonics ? 0.6 * Math.sin(2 * ph) + 0.3 * Math.sin(3 * ph) : 0));
  }
  return buf;
}

Deno.test("detectPitch (YIN): senoides e voz com harmônicos, erro < 5 cents e confiança alta", () => {
  for (const f of [82.41, 110, 220, 261.63, 440, 659.25, 987.77]) {
    for (const harmonics of [false, true]) {
      const r = detectPitch(tone(f, { harmonics }), SR);
      const cents = Math.abs(freqToMidi(r.freq) - freqToMidi(f)) * 100;
      ok(cents < 5, `${f} Hz${harmonics ? " c/ harmônicos" : ""}: erro ${cents.toFixed(2)} cents`);
      ok(r.confidence > 0.85, `${f} Hz confiança ${r.confidence}`);
    }
  }
});

Deno.test("detectPitch: silêncio e ruído não dão nota", () => {
  eq(detectPitch(new Float32Array(2048), SR).freq, null, "silêncio");
  eq(detectPitch(tone(220, { amp: 0.005 }), SR).freq, null, "abaixo do RMS mínimo");
  let seed = 7;
  const noise = new Float32Array(2048).map(() =>
    ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5) * 0.6
  );
  const r = detectPitch(noise, SR);
  ok(r.freq === null || r.confidence < 0.6, `ruído branco: ${r.freq} Hz conf ${r.confidence}`);
});

Deno.test("deslocamento de oitava (tecla O): ciclo, leitura salva, HUD, faixa da régua", () => {
  eq([0, -1, 1].map(nextOctaveShift), [-1, 1, 0]);
  eq(nextOctaveShift(7), 0, "valor estranho volta ao início do ciclo");
  eq([parseOctaveShift("-1"), parseOctaveShift("1"), parseOctaveShift(null), parseOctaveShift("x")], [
    -1,
    1,
    0,
    0,
  ]);
  eq(octaveShiftLabel(-1), "Régua: 1 oitava abaixo");
  eq(octaveShiftLabel(1), "Régua: 1 oitava acima");
  eq(midiRange([{ midi: 60 }, { midi: 72 }], -1), { min: 46, max: 62 }, "barras uma oitava abaixo");
  eq(
    midiRange([{ midi: 80 }], 1),
    { min: 84, max: 96 },
    "acima de 84 com deslocamento +1, sem passar do teto",
  );
});

Deno.test("midiRange: justa às notas, ≥ 1 oitava, padrão C3–B5", () => {
  eq(midiRange([]), { min: 48, max: 83 });
  eq(midiRange([{ midi: 60 }, { midi: 72 }]), { min: 58, max: 74 });
  const r = midiRange([{ midi: 64 }]);
  ok(r.max - r.min === 12 && r.min <= 64 && r.max >= 64, JSON.stringify(r));
});

// ------------------------------------------------------------------ suavização (US-25)

const FRAME_MS = 1000 / 60;
const voiced = (midi) => ({ midi, confidence: 0.95, rms: 0.05 });
const silent = { midi: null, confidence: 0, rms: 0.001 };
function rng(seed) {
  return () => (seed = (seed * 16807) % 2147483647) / 2147483647;
}
function gauss(rand) {
  return Math.sqrt(-2 * Math.log(rand() || 1e-9)) * Math.cos(2 * Math.PI * rand());
}
function std(xs) {
  const m = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / xs.length);
}

Deno.test("smoother: leitura com tremor de 30 cents → desvio ≤ 15 cents", () => {
  for (const fps of [60, 144]) {
    const sm = new PitchSmoother();
    const rand = rng(11);
    const out = [];
    const dtMs = 1000 / fps;
    for (let i = 0; i < fps * 4; i++) {
      const o = sm.update(voiced(60 + (gauss(rand) * 30) / 100), i * dtMs);
      if (i > fps * 0.5) out.push(o.midi * 100);
    }
    const sd = std(out);
    ok(sd <= 15, `${fps} Hz: desvio ${sd.toFixed(1)} cents`);
  }
});

Deno.test("smoother: vibrato leve (±25 cents, 5,5 Hz) fica centrado na nota", () => {
  const sm = new PitchSmoother();
  const out = [];
  for (let i = 0; i < 240; i++) {
    const t = (i * FRAME_MS) / 1000;
    const o = sm.update(voiced(64 + 0.25 * Math.sin(2 * Math.PI * 5.5 * t)), i * FRAME_MS);
    if (i > 30) out.push(o.midi);
  }
  const mean = out.reduce((a, b) => a + b, 0) / out.length;
  ok(Math.abs(mean - 64) * 100 < 5, `média ${mean}`);
  ok(std(out.map((m) => m * 100)) <= 15, "desvio do vibrato");
});

Deno.test("smoother: salto isolado de oitava (1–2 leituras) é descartado", () => {
  for (const glitch of [1, 2]) {
    const sm = new PitchSmoother();
    let worst = 0;
    for (let i = 0; i < 60; i++) {
      const bad = i >= 30 && i < 30 + glitch;
      const o = sm.update(voiced(bad ? 72 : 60), i * FRAME_MS);
      if (i > 5) worst = Math.max(worst, Math.abs(o.midi - 60) * 100);
    }
    ok(worst < 5, `glitch de ${glitch}: desvio máx ${worst.toFixed(1)} cents`);
  }
});

Deno.test("smoother: transição real (2, 5 e 12 semitons) aparece em ≤ 80 ms", () => {
  for (const step of [2, 5, 12, -12]) {
    for (const fps of [60, 144]) {
      const sm = new PitchSmoother();
      const dtMs = 1000 / fps;
      let i = 0;
      for (; i < fps; i++) sm.update(voiced(60), i * dtMs);
      const t0 = i * dtMs;
      let reached = null;
      for (; i < fps * 2 && reached == null; i++) {
        const o = sm.update(voiced(60 + step), i * dtMs);
        if (Math.abs(o.midi - (60 + step)) * 100 <= 25) reached = i * dtMs - t0;
      }
      ok(reached != null && reached <= 80, `${step} st a ${fps} Hz: ${reached} ms`);
    }
  }
});

Deno.test("smoother: ao calar mantém ~120 ms, some suave e nunca pontua no silêncio", () => {
  const sm = new PitchSmoother();
  let t = 0;
  for (let i = 0; i < 30; i++, t += FRAME_MS) sm.update(voiced(62), t);
  const stop = t - FRAME_MS; // última leitura sonora
  let o = sm.update(silent, t);
  ok(!o.voiced && o.midi != null && o.alpha === 1, "logo após calar: segura, mas não é sonoro");
  o = sm.update(silent, stop + HOLD_MS - 5);
  ok(o.alpha === 1 && Math.abs(o.midi - 62) < 0.05, "ainda no hold");
  o = sm.update(silent, stop + HOLD_MS + FADE_MS / 2);
  ok(o.alpha > 0.3 && o.alpha < 0.7, `fade no meio: ${o.alpha}`);
  o = sm.update(silent, stop + HOLD_MS + FADE_MS + 1);
  eq(o.midi, null, "sumiu");
});

Deno.test("smoother: histerese — nível oscilando perto do limiar não pisca; baixa confiança não liga", () => {
  const sm = new PitchSmoother();
  let flips = 0;
  let last = false;
  for (let i = 0; i < 120; i++) {
    const rms = i < 5 ? 0.05 : 0.009 + 0.004 * Math.sin(i); // entre 0,005 e 0,013, com início forte
    const o = sm.update({ midi: 60, confidence: 0.9, rms: Math.max(0.0075, rms) }, i * FRAME_MS);
    if (o.voiced !== last) flips++;
    last = o.voiced;
  }
  eq(flips, 1, "liga uma vez e fica");
  const quiet = new PitchSmoother();
  ok(!quiet.update({ midi: 60, confidence: 0.5, rms: 0.05 }, 0).voiced, "confiança baixa não liga");
});

Deno.test("pontuação usa a leitura filtrada: tremor ±40 cents em torno da nota ainda acerta", () => {
  const sm = new PitchSmoother();
  const rand = rng(5);
  let hits = 0;
  let n = 0;
  for (let i = 0; i < 300; i++) {
    const o = sm.update(voiced(60 + (gauss(rand) * 40) / 100), i * FRAME_MS);
    if (i > 30) {
      n++;
      if (checkPitchHit(60, o.voiced ? o.midi : null)) hits++;
    }
  }
  ok(hits / n > 0.97, `acertos ${hits}/${n}`);
});
