/**
 * Busca enquanto digita (DECISION 0019, US-14) — lógica pura, sem DOM.
 *
 * - `input(q)`: agenda a busca `delayMs` depois da última tecla (só com ≥ `minChars` caracteres).
 *   Abaixo do mínimo: cancela o agendado e a requisição em andamento.
 * - `now(q)`: busca imediata (Enter/botão), cancelando o debounce; aceita qualquer texto não vazio.
 * - Cada busca nova aborta a anterior (`AbortController`) e respostas fora de ordem são descartadas.
 * - `pauseUntil(ts)`: após um 429, segura as buscas até `ts` e então busca o texto mais recente.
 * - Não repete a busca do texto que já está em andamento ou que já respondeu com sucesso.
 *
 * Dono: frontend-engineer (FIX-004, ciclo 20261005-0109).
 */

/** Normaliza o texto da busca (espaços repetidos, pontas). */
export function cleanQuery(s) {
  return String(s || "").replace(/\s+/g, " ").trim();
}

/** Quantidade de caracteres (não bytes/UTF-16). */
export function charCount(s) {
  return [...String(s || "")].length;
}

/** Timers reais (embrulhados: `setTimeout` chamado como método de outro objeto falha no navegador). */
function defaultClock() {
  return {
    setTimeout: (fn, ms) => setTimeout(fn, ms),
    clearTimeout: (t) => clearTimeout(t),
    now: () => Date.now(),
  };
}

/**
 * @param {object} o
 * @param {(q: string, signal: AbortSignal) => Promise<unknown>} o.run faz a requisição
 * @param {(q: string) => void} [o.onStart]
 * @param {(q: string, res: unknown) => void} [o.onResult]
 * @param {(q: string, err: unknown) => void} [o.onError] nunca chamado para abortadas/obsoletas
 * @param {number} [o.delayMs]
 * @param {number} [o.minChars]
 * @param {{setTimeout: Function, clearTimeout: Function, now: () => number}} [o.clock] injetável nos testes
 */
export function createLiveSearch(o) {
  const delayMs = o.delayMs ?? 700;
  const minChars = o.minChars ?? 3;
  const clock = o.clock || defaultClock();
  let timer = null;
  let ctrl = null;
  let seq = 0;
  let active = ""; // texto em andamento ou último com sucesso
  let wanted = ""; // texto mais recente pedido
  let blockedUntil = 0;

  function clearTimer() {
    if (timer !== null) clock.clearTimeout(timer);
    timer = null;
  }

  function abort() {
    seq++;
    if (ctrl) ctrl.abort();
    ctrl = null;
    active = "";
  }

  function schedule(ms) {
    clearTimer();
    timer = clock.setTimeout(() => {
      timer = null;
      start(wanted);
    }, ms);
  }

  function start(q) {
    if (!q) return;
    const wait = blockedUntil - clock.now();
    if (wait > 0) return schedule(wait);
    if (q === active) return;
    abort();
    const my = seq;
    const c = typeof AbortController === "function" ? new AbortController() : null;
    ctrl = c;
    active = q;
    if (o.onStart) o.onStart(q);
    let p;
    try {
      p = Promise.resolve(o.run(q, c ? c.signal : undefined));
    } catch (err) {
      p = Promise.reject(err);
    }
    p.then(
      (res) => {
        if (my !== seq) return;
        ctrl = null;
        if (o.onResult) o.onResult(q, res);
      },
      (err) => {
        if (my !== seq) return;
        ctrl = null;
        active = "";
        if (err && err.name === "AbortError") return;
        if (o.onError) o.onError(q, err);
      },
    );
  }

  return {
    /** Tecla digitada. Devolve true se uma busca foi agendada. */
    input(text) {
      const q = cleanQuery(text);
      wanted = q;
      if (charCount(q) < minChars) {
        clearTimer();
        abort();
        return false;
      }
      if (q === active) {
        clearTimer();
        return false;
      }
      schedule(delayMs);
      return true;
    },
    /** Enter/botão: busca já (ou assim que o 429 liberar). `force` refaz mesmo se igual à atual. */
    now(text, force = false) {
      const q = cleanQuery(text);
      wanted = q;
      clearTimer();
      if (!q) {
        abort();
        return;
      }
      if (force) active = "";
      start(q);
    },
    /** 429: segura até `ts` (epoch ms) e então busca o texto mais recente. */
    pauseUntil(ts) {
      blockedUntil = ts;
      active = "";
      if (wanted) schedule(Math.max(0, ts - clock.now()));
    },
    /** Cancela tudo (campo limpo, saída da tela). */
    cancel() {
      wanted = "";
      clearTimer();
      abort();
    },
    /** Há busca agendada ou em andamento. */
    get pending() {
      return timer !== null || ctrl !== null;
    },
  };
}

/** Debounce simples (ex.: filtro da Biblioteca a ~150 ms). */
export function debounce(fn, ms, clock = defaultClock()) {
  let t = null;
  const d = (...args) => {
    if (t !== null) clock.clearTimeout(t);
    t = clock.setTimeout(() => {
      t = null;
      fn(...args);
    }, ms);
  };
  d.flush = (...args) => {
    if (t !== null) clock.clearTimeout(t);
    t = null;
    fn(...args);
  };
  return d;
}
