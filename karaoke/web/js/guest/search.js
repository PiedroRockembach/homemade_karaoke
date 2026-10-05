/**
 * Aba "Buscar" (DESIGN §12.4 G2, US-14) — busca enquanto digita (DECISION 0019):
 * "Na Biblioteca" filtra em ~150 ms; "No YouTube" (`GET /api/search`) ~700 ms após a última tecla (≥ 3
 * caracteres), abortando a anterior. Enter = busca imediata. 429: mantém os resultados, dica discreta e
 * nova tentativa automática após `retry_after_s`. Sem cota de pedidos (DECISION 0016).
 *
 * Dono: frontend-engineer (T-006, FIX-004; ciclo 20261005-0109).
 */

import { api, qs } from "../shared/api.js";
import { fmtTime } from "../shared/format.js";
import { h, icon, preserveFocus, replace } from "../shared/components/dom.js";
import { charCount, createLiveSearch, debounce } from "../shared/live-search.js";
import {
  cleanName,
  errorText,
  filterLibrary,
  limitsOf,
  queuedSongIds,
  resultKind,
  retryAfterSeconds,
  sessionScreenFor,
} from "./logic.js";
import { callout, thumb } from "./ui.js";
import { openRequestSheet } from "./request-sheet.js";

const LIB_DELAY_MS = 150; // filtro da Biblioteca enquanto digita
const YT_DELAY_MS = 700; // busca no YouTube depois da última tecla
const YT_MIN_CHARS = 3;

const CHIP = {
  ready: ["chip-success", "check", "Pronta"],
  processing: ["chip-info", "clock", "Preparando"],
  new: ["chip-info", "clock", "Nova · ~1 min"],
  long: ["chip-warning", "alert", "Vídeo longo"],
  live: ["chip-danger", null, "Ao vivo"],
};
const WHY = {
  long: "Na festa, só vídeos de até 15 min.",
  live: "Transmissões ao vivo não podem ser cantadas.",
};

function chip(kind) {
  const [cls, ic, text] = CHIP[kind];
  return h("span", { class: ["chip", cls] }, ic ? icon(ic) : null, text);
}

function skeletonRows(n) {
  return Array.from({ length: n }, () =>
    h(
      "li",
      { class: "row", "aria-hidden": "true" },
      h("div", { class: "thumb skel" }),
      h(
        "div",
        { class: "txt" },
        h("div", { class: "skel skel-line" }),
        h("div", { class: "skel skel-line short" }),
      ),
      h("div", { class: "skel skel-btn" }),
    ));
}

export function initSearch(ctx, root) {
  const input = h("input", {
    id: "q",
    name: "q",
    class: "input",
    type: "search",
    enterkeyhint: "search",
    placeholder: "Música ou artista…",
    autocomplete: "off",
    maxlength: "200",
  });
  const clearBtn = h("button", {
    type: "button",
    class: "clr",
    "aria-label": "Limpar busca",
    hidden: true,
    onclick: () => {
      input.value = "";
      reset();
      input.focus();
    },
  }, icon("x"));
  const form = h(
    "form",
    { role: "search", novalidate: true, onsubmit: (e) => submit(e) },
    h(
      "div",
      { class: "wrap" },
      icon("search"),
      h("label", { class: "sr-only", for: "q" }, "Buscar música"),
      input,
      clearBtn,
    ),
  );
  const top = h("div", { class: "search" }, form);
  const results = h("div", { class: "results" });
  // Anúncio para leitor de tela só quando o YouTube responde (não a cada tecla).
  const status = h("p", { class: "sr-only", role: "status", "aria-live": "polite" });
  // Indicador discreto da seção "No YouTube" (buscando… / 429), atualizado no lugar.
  const ytHint = h("span", { class: "yt-hint", id: "yt-hint" });
  replace(root, top, status, results);

  const st = {
    typed: "", // texto já aplicado ao filtro da Biblioteca (~150 ms)
    lib: { status: "loading", songs: [] },
    yt: { status: "idle" }, // idle | loading | ok | error | rate; `busy` = buscando com resultados na tela
    rateUntil: 0,
    announce: false,
  };
  let rateTimer = 0;
  let sig = "";

  // ---------------------------------------------------------------- dados
  async function loadLibrary() {
    try {
      const res = await api.get("/api/songs" + qs({ status: "ready", sort: "title" }));
      st.lib = { status: "ok", songs: res.songs || [] };
    } catch (err) {
      if (sessionScreenFor(err)) return ctx.handleSessionError(err);
      if (st.lib.status !== "ok") st.lib = { status: "error", songs: [] };
    }
    render(true);
  }

  const live = createLiveSearch({
    delayMs: YT_DELAY_MS,
    minChars: YT_MIN_CHARS,
    run: (q, signal) => api.get("/api/search" + qs({ q, limit: 15 }), { signal }),
    onStart(q) {
      st.yt = st.yt.status === "ok" ? { ...st.yt, busy: true } : { status: "loading", q };
      render(true);
    },
    onResult(q, res) {
      st.yt = { status: "ok", q, results: res.results || [], busy: false };
      st.announce = true;
      render(true);
    },
    onError(q, err) {
      if (sessionScreenFor(err)) return ctx.handleSessionError(err);
      if (err && err.code === "rate_limited") {
        // Sem erro alarmante: mantém o que já está na tela e tenta de novo sozinho.
        st.rateUntil = Date.now() + retryAfterSeconds(err) * 1000;
        st.yt = st.yt.status === "ok" ? { ...st.yt, busy: false } : { status: "rate", q };
        live.pauseUntil(st.rateUntil);
        tickRate();
      } else {
        st.yt = { status: "error", q, message: errorText(err, "search") };
      }
      render(true);
    },
  });

  const applyFilter = debounce(() => {
    st.typed = cleanName(input.value);
    render(true);
  }, LIB_DELAY_MS);

  function reset() {
    live.cancel();
    applyFilter.flush();
    st.yt = { status: "idle" };
    clearBtn.hidden = true;
    render(true);
  }

  function tickRate() {
    clearTimeout(rateTimer);
    updateHint();
    if (Date.now() < st.rateUntil) rateTimer = setTimeout(tickRate, 1000);
  }

  function submit(e) {
    e.preventDefault();
    clearBtn.hidden = !input.value;
    if (!cleanName(input.value)) return reset();
    applyFilter.flush();
    input.blur(); // fecha o teclado para mostrar os resultados
    live.now(input.value);
  }

  input.addEventListener("input", () => {
    clearBtn.hidden = !input.value;
    const q = cleanName(input.value);
    if (!q) return reset();
    if (live.input(q)) {
      if (st.yt.status !== "ok") st.yt = { status: "loading", q };
    } else if (charCount(q) < YT_MIN_CHARS && st.yt.status !== "idle") {
      st.yt = { status: "idle" };
    }
    applyFilter();
    updateHint();
  });

  // ---------------------------------------------------------------- render
  function onRequest(target, opener) {
    openRequestSheet(ctx, target, opener);
  }

  function pedirBtn(target) {
    const btn = h("button", {
      type: "button",
      class: "btn btn-secondary",
      "aria-label": `Pedir ${target.title}`,
      "data-focus-key": `pedir-${target.source}-${target.id}`,
      onclick: (e) => {
        e.stopPropagation();
        onRequest(target, btn);
      },
    }, "Pedir");
    return btn;
  }

  function libRow(s, inQueue) {
    const target = {
      kind: "ready",
      source: "lib",
      id: s.id,
      title: s.title,
      artist: s.artist || "",
      duration_ms: s.duration_ms,
      thumbnail: s.thumbnail,
    };
    const btn = pedirBtn(target);
    return h(
      "li",
      { class: "row tap", onclick: () => onRequest(target, btn) },
      thumb(s.thumbnail),
      h(
        "div",
        { class: "txt" },
        h("div", { class: "t", title: s.title }, s.title),
        h(
          "div",
          { class: "m" },
          h("span", {}, [s.artist, s.duration_ms ? fmtTime(s.duration_ms) : ""].filter(Boolean).join(" · ")),
          chip("ready"),
          inQueue ? h("span", { class: "chip chip-neutral" }, "Já na fila") : null,
        ),
      ),
      btn,
    );
  }

  function ytRow(r, limits, inQueue) {
    const kind = resultKind(r, limits.max_duration_ms);
    const target = {
      kind,
      source: "yt",
      id: r.youtube_id,
      title: r.title,
      artist: r.channel || "",
      duration_ms: r.duration_ms,
      thumbnail: r.thumbnail,
    };
    const noButton = kind === "live" || kind === "long";
    return h(
      "li",
      { class: "row" },
      thumb(r.thumbnail),
      h(
        "div",
        { class: "txt" },
        h("div", { class: "t", title: r.title }, r.title),
        h(
          "div",
          { class: "m" },
          h("span", {}, [r.channel, r.duration_ms ? fmtTime(r.duration_ms) : ""].filter(Boolean).join(" · ")),
          chip(kind),
          inQueue && !noButton ? h("span", { class: "chip chip-neutral" }, "Já na fila") : null,
        ),
        WHY[kind] ? h("div", { class: "why" }, WHY[kind]) : null,
      ),
      noButton ? null : pedirBtn(target),
    );
  }

  /** Texto do indicador do YouTube: contagem do 429 ou "buscando…". */
  function updateHint() {
    const left = Math.ceil((st.rateUntil - Date.now()) / 1000);
    let text = "";
    if (left > 0) text = `Muitas buscas — de novo em ${left} s`;
    else if (st.yt.status === "ok" && (st.yt.busy || live.pending)) text = "buscando…";
    ytHint.textContent = text;
    ytHint.classList.toggle("is-busy", text === "buscando…");
  }

  function section(title, body, attrs = {}, extra = null) {
    return h(
      "section",
      { class: "sec", ...attrs },
      h("div", { class: "sec-top" }, h("h2", { class: "sec-h" }, title), extra),
      body,
    );
  }

  function libError() {
    return callout("danger", "wifi-off", "Não deu para carregar as músicas prontas.", [
      h(
        "button",
        { type: "button", class: "btn btn-secondary", onclick: loadLibrary },
        icon("refresh"),
        "Tentar de novo",
      ),
    ]);
  }

  function buildResults(limits) {
    const queued = queuedSongIds(ctx.store.get().queue);
    const typed = st.typed;
    const yt = st.yt;
    const searching = typed && (yt.status !== "idle" || charCount(typed) >= YT_MIN_CHARS);

    // Sem busca no YouTube: Prontas para cantar (filtradas pelo que está digitado).
    if (!searching) {
      if (st.lib.status === "loading") {
        return section("Prontas para cantar", h("ul", { class: "list" }, skeletonRows(3)), {
          "aria-busy": "true",
        });
      }
      if (st.lib.status === "error") return libError();
      const songs = filterLibrary(st.lib.songs, typed);
      if (!st.lib.songs.length) {
        return h(
          "div",
          { class: "empty" },
          h("div", { class: "ring" }, icon("up")),
          h("h2", {}, "O que vamos cantar?"),
          h(
            "p",
            {},
            "Ainda não há músicas prontas. Busque qualquer música acima — ela é preparada em cerca de 1 minuto.",
          ),
        );
      }
      const list = songs.length
        ? h("ul", { class: "list" }, songs.map((s) => libRow(s, queued.has(s.id))))
        : h("p", { class: "small pad" }, "Nenhuma pronta com esse nome.");
      return [
        typed ? null : h("p", { class: "lead-q" }, "O que vamos cantar?"),
        section(`Prontas para cantar · ${songs.length}`, list),
        typed
          ? h("p", { class: "small pad" }, "Digite pelo menos 3 letras para buscar também no YouTube.")
          : null,
      ];
    }

    // Digitando: Biblioteca (instantânea) e YouTube (debounce).
    const lib = st.lib.status === "ok" ? filterLibrary(st.lib.songs, typed) : [];
    const libIds = new Set(lib.map((s) => s.id));
    const ytList = yt.status === "ok" ? yt.results.filter((r) => !libIds.has(r.youtube_id)) : [];
    if (st.announce) {
      st.announce = false;
      status.textContent = `${lib.length} na Biblioteca e ${ytList.length} no YouTube para “${yt.q}”.`;
    }
    if (yt.status === "ok" && !yt.busy && !live.pending && !lib.length && !ytList.length) {
      return h(
        "div",
        { class: "empty" },
        h("div", { class: "ring" }, icon("search")),
        h("h2", {}, "Nada encontrado"),
        h("p", {}, `Nada encontrado para "${yt.q}". Tente só o nome do artista.`),
      );
    }

    const libBody = st.lib.status === "error"
      ? libError()
      : lib.length
      ? h("ul", { class: "list" }, lib.map((s) => libRow(s, queued.has(s.id))))
      : h("p", { class: "small pad" }, "Nenhuma pronta com esse nome.");

    let ytBody;
    if (yt.status === "loading") ytBody = h("ul", { class: "list" }, skeletonRows(4));
    else if (yt.status === "error") {
      ytBody = callout("danger", "wifi-off", yt.message, [
        h(
          "button",
          { type: "button", class: "btn btn-secondary", onclick: () => live.now(input.value, true) },
          icon("refresh"),
          "Tentar de novo",
        ),
      ]);
    } else if (yt.status === "rate") {
      ytBody = h("p", { class: "small pad" }, "A busca no YouTube volta em instantes.");
    } else if (yt.status === "ok") {
      ytBody = ytList.length
        ? h("ul", { class: "list" }, ytList.map((r) => ytRow(r, limits, queued.has(r.youtube_id))))
        : h("p", { class: "small pad" }, "Nenhum vídeo além dos da Biblioteca.");
    } else ytBody = h("ul", { class: "list" }, skeletonRows(4));

    updateHint();
    return [
      section(`Na Biblioteca · ${lib.length}`, libBody),
      section(
        "No YouTube",
        ytBody,
        { "aria-busy": yt.status === "loading" || yt.busy ? "true" : "false", "aria-describedby": "yt-hint" },
        ytHint,
      ),
    ];
  }

  /** Redesenha; sem `force`, só quando algo que aparece mudou (evita refazer capas a cada progresso). */
  function render(force = false) {
    const s = ctx.store.get();
    const limits = limitsOf(s.guest);
    const next = JSON.stringify([limits, [...queuedSongIds(s.queue)]]);
    updateHint();
    if (!force && next === sig) return;
    sig = next;
    preserveFocus(results, () => replace(results, buildResults(limits)));
  }

  return {
    render,
    loadLibrary,
    focusInput: () => input.focus(),
  };
}
