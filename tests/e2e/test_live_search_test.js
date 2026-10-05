// Testes puros da busca enquanto digita (FIX-004, DECISION 0019): debounce, mínimo de caracteres, abort,
// respostas fora de ordem, Enter imediato e pausa após 429.
// Rodar: .venv/bin/deno test --no-check tests/e2e/test_live_search_test.js
// Executado também por tests/e2e/test_stage_js.py (make test). Dono: frontend-engineer (FIX-004).
import {
  charCount,
  cleanQuery,
  createLiveSearch,
  debounce,
} from "../../karaoke/web/js/shared/live-search.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}

/** Relógio falso: timers disparam só com `advance(ms)`. */
function fakeClock() {
  let t = 0;
  let id = 0;
  const timers = new Map();
  return {
    now: () => t,
    setTimeout(fn, ms) {
      timers.set(++id, { at: t + ms, fn });
      return id;
    },
    clearTimeout(i) {
      timers.delete(i);
    },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, x]) => x.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]);
        t = due[1].at;
        due[1].fn();
      }
      t = end;
    },
  };
}

/** Requisições controladas pelo teste: cada uma pode ser resolvida/rejeitada fora de ordem. */
function harness(opts = {}) {
  const clock = fakeClock();
  const calls = [];
  const log = [];
  const live = createLiveSearch({
    clock,
    delayMs: 700,
    minChars: 3,
    ...opts,
    run(q, signal) {
      return new Promise((resolve, reject) => {
        const c = { q, signal, resolve, reject, aborted: false };
        if (signal) {
          signal.addEventListener("abort", () => {
            c.aborted = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        }
        calls.push(c);
      });
    },
    onStart: (q) => log.push(["start", q]),
    onResult: (q, r) => log.push(["ok", q, r]),
    onError: (q, e) => log.push(["err", q, e.code || e.name]),
  });
  return { clock, calls, log, live };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

Deno.test("cleanQuery/charCount", () => {
  eq(cleanQuery("  a   b "), "a b");
  eq(charCount("ção"), 3);
  eq(charCount("🎤🎤"), 2);
});

Deno.test("debounce de 700 ms: só a última tecla dispara", () => {
  const { clock, calls, live } = harness();
  live.input("abc");
  clock.advance(400);
  live.input("abcd");
  clock.advance(699);
  eq(calls.length, 0, "antes do prazo");
  clock.advance(1);
  eq(calls.map((c) => c.q), ["abcd"]);
});

Deno.test("menos de 3 caracteres não busca e cancela a pendente", () => {
  const { clock, calls, live } = harness();
  eq(live.input("ab"), false);
  clock.advance(2000);
  eq(calls.length, 0);
  live.input("abc");
  clock.advance(700);
  eq(calls.length, 1);
  live.input("ab");
  eq(calls[0].aborted, true, "abortou a em andamento");
  eq(live.pending, false);
});

Deno.test("nova busca aborta a anterior e respostas obsoletas são descartadas", async () => {
  const { clock, calls, log, live } = harness();
  live.input("rock");
  clock.advance(700);
  live.input("rock nacional");
  clock.advance(700);
  eq(calls[0].aborted, true);
  calls[0].resolve("velha"); // chega depois: ignorada
  calls[1].resolve("nova");
  await tick();
  eq(log.filter((x) => x[0] !== "start"), [["ok", "rock nacional", "nova"]]);
});

Deno.test("fora de ordem sem AbortController efetivo: só vale a mais recente", async () => {
  const clock = fakeClock();
  const pend = [];
  const log = [];
  const live = createLiveSearch({
    clock,
    run: (q) => new Promise((resolve) => pend.push({ q, resolve })), // ignora o signal
    onResult: (q, r) => log.push([q, r]),
  });
  live.now("abc");
  live.now("abcd");
  pend[1].resolve(2);
  pend[0].resolve(1);
  await tick();
  eq(log, [["abcd", 2]]);
});

Deno.test("Enter busca já, cancelando o debounce; aceita menos de 3 caracteres", () => {
  const { clock, calls, live } = harness();
  live.input("abcde");
  live.now("ab");
  eq(calls.map((c) => c.q), ["ab"]);
  clock.advance(5000);
  eq(calls.length, 1, "o debounce não dispara depois");
});

Deno.test("não repete a busca do mesmo texto (em andamento ou já respondida)", async () => {
  const { clock, calls, live } = harness();
  live.input("samba");
  clock.advance(700);
  live.input("samba ");
  clock.advance(700);
  eq(calls.length, 1, "em andamento");
  calls[0].resolve("x");
  await tick();
  live.input("samba");
  clock.advance(700);
  eq(calls.length, 1, "já respondida");
  live.now("samba", true);
  eq(calls.length, 2, "force refaz");
});

Deno.test("erro libera para tentar o mesmo texto de novo; AbortError nunca vira erro", async () => {
  const { clock, calls, log, live } = harness();
  live.input("forro");
  clock.advance(700);
  calls[0].reject({ code: "network_error" });
  await tick();
  eq(log.at(-1), ["err", "forro", "network_error"]);
  live.input("forro");
  clock.advance(700);
  eq(calls.length, 2);
  live.cancel();
  await tick();
  eq(log.filter((x) => x[0] === "err").length, 1);
});

Deno.test("429: pausa até o Retry-After e então busca o texto mais recente", async () => {
  const { clock, calls, live } = harness();
  live.input("pagode");
  clock.advance(700);
  calls[0].reject({ code: "rate_limited" });
  await tick();
  live.pauseUntil(clock.now() + 5000);
  live.input("pagode 90");
  clock.advance(700);
  eq(calls.length, 1, "segura durante a pausa");
  clock.advance(4299);
  eq(calls.length, 1);
  clock.advance(1);
  eq(calls.map((c) => c.q), ["pagode", "pagode 90"]);
});

Deno.test("429 sem digitar mais: refaz a mesma busca sozinho", async () => {
  const { clock, calls, live } = harness();
  live.now("axe");
  calls[0].reject({ code: "rate_limited" });
  await tick();
  live.pauseUntil(clock.now() + 3000);
  clock.advance(3000);
  eq(calls.map((c) => c.q), ["axe", "axe"]);
});

Deno.test("cancel limpa agendada e em andamento", () => {
  const { clock, calls, live } = harness();
  live.now("abc");
  live.input("abcd");
  live.cancel();
  clock.advance(1000);
  eq(calls.length, 1);
  eq(calls[0].aborted, true);
  eq(live.pending, false);
});

Deno.test("debounce simples + flush", () => {
  const clock = fakeClock();
  const got = [];
  const d = debounce((x) => got.push(x), 150, clock);
  d(1);
  d(2);
  clock.advance(150);
  d(3);
  d.flush(4);
  clock.advance(500);
  eq(got, [2, 4]);
});
