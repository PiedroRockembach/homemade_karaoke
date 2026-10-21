/**
 * MOCK do servidor para o Palco — ativado só por `/stage?mock=1`. Isolado e fácil de remover
 * (apagar este arquivo e o bloco `mock` em main.js). Segue o contrato (api.md §9–10, lyrics-format.md):
 * PlayerState, QueueItem, Song, eventos `hello` / `player.state` / `player.position` / `queue.updated`,
 * `POST /api/stage/claim`, `/api/player/command`, `/api/player/report`, `GET /api/songs/{id}/lyrics`.
 * O áudio é sintetizado no navegador (instrumental + "voz guia" em tons nas palavras da letra).
 *
 * Parâmetros extras (revisão/screenshot):
 *   autostart=1        simula o Enter ao abrir (precisa de autoplay liberado no navegador)
 *   mockq=empty        fila vazia (tela de espera)
 *   mocklyrics=none    1ª música sem letra ("Letra indisponível")
 *   mockpitch=none     sem notas (`GET /api/songs/{id}/pitch` → notes: []) — "Notas indisponíveis…"
 *   mockerr=1          1ª música com áudio quebrado (tela de erro)
 *   mockprep=1         fila com 1 música ainda processando ("Aguardando preparo"; fica pronta em ~15 s)
 *   mockowner=other    outro Palco é o dono do áudio (modo espelho)
 *   mockstart=<s>      a 1ª música começa nessa posição (ex.: 25 = intervalo instrumental; 40 = perto do fim)
 *   mockpaused=1       a 1ª música começa pausada
 *   autoplay=0         auto-avanço desligado ("A seguir" manual)
 *   countdown=<s>      duração do "A seguir" (padrão 8)
 *
 * Dono: frontend-engineer (T-007).
 */

import { setTransport } from "../shared/api.js";
import { setEventSourceFactory } from "../shared/events.js";
import { prepareLyrics } from "./lyrics-timing.js";

// ------------------------------------------------------------------ dados fictícios (autorais)

const ARTS = [
  ["#ff8a3d", "#7b2cff", "#1a0b2e"],
  ["#00e0ff", "#ff2f92", "#0b1030"],
  ["#3ddc97", "#2b6cff", "#06141f"],
];

function artUri(i) {
  const [a, b, bg] = ARTS[i % ARTS.length];
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 90"><defs>` +
    `<radialGradient id="a" cx=".3" cy=".3" r=".6"><stop offset="0" stop-color="${a}"/>` +
    `<stop offset="1" stop-color="${a}" stop-opacity="0"/></radialGradient>` +
    `<radialGradient id="b" cx=".75" cy=".7" r=".6"><stop offset="0" stop-color="${b}"/>` +
    `<stop offset="1" stop-color="${b}" stop-opacity="0"/></radialGradient></defs>` +
    `<rect width="160" height="90" fill="${bg}"/><rect width="160" height="90" fill="url(#a)"/>` +
    `<rect width="160" height="90" fill="url(#b)"/></svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}

const SONGS = [
  {
    id: "mockCancao1",
    title: "Canção da Estrada Vazia",
    artist: "Os Faróis",
    duration: 42.5,
    timing: "line",
    lines: [
      [6.0, "Saí de casa sem olhar pra trás"],
      [9.6, "A estrada inteira só pra mim"],
      [13.2, "O rádio toca aquela canção"],
      [16.8, "Que a gente cantava sem ter fim"],
      [27.5, "E quando a noite acender o céu"],
      [31.0, "Eu vou cantar até o sol nascer"],
      [34.6, "Luzes da cidade, luzes do farol"],
      [38.2, "Tudo que eu preciso é você"],
    ],
  },
  {
    id: "mockLuzes01",
    title: "Luzes de Neon",
    artist: "Banda Madrugada",
    duration: 43.0,
    timing: "word",
    lines: [
      [3.0, "Neon na avenida acende o meu olhar"],
      [7.0, "A noite é uma pista pra gente dançar"],
      [11.0, "Ninguém vai dormir antes do amanhecer"],
      [15.0, "Cada letreiro pisca só pra você"],
      [26.5, "Liga o som, aumenta mais"],
      [30.0, "Deixa a cidade lá pra trás"],
      [33.5, "Luzes de neon no céu"],
      [37.0, "Brilham como um carrossel"],
    ],
  },
  {
    id: "mockDanca01",
    title: "Dança no Telhado",
    artist: "Os Vizinhos",
    duration: 36.0,
    timing: "line",
    lines: [
      [4.0, "Subi no telhado pra ver o luar"],
      [8.0, "A vizinhança inteira veio dançar"],
      [12.0, "Uma antena velha virou microfone"],
      [16.0, "E a lua cheia sabe o nosso nome"],
      [24.0, "Dança no telhado, ninguém vai cair"],
      [28.0, "Segura minha mão e vem sorrir"],
    ],
  },
];

const OTHER_STAGE = "c_outro_palco";
const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const round2 = (v) => Math.round(v * 100) / 100;

function lyricsDoc(def, opts) {
  if (opts.noLyrics && def.id === SONGS[0].id) {
    return {
      version: 1,
      song_id: def.id,
      candidate_id: null,
      source: null,
      timing: "none",
      language: null,
      duration_ms: def.duration * 1000,
      offset_ms: 0,
      lines: [],
      meta: {},
    };
  }
  const starts = def.lines.map(([t]) => Math.round(t * 1000));
  const lines = def.lines.map(([, text], i) => {
    const start = starts[i];
    const nWords = text.split(" ").length;
    const nextTs = i + 1 < starts.length ? starts[i + 1] : def.duration * 1000;
    const end = Math.min(nextTs, start + clamp(nWords * 450 + 600, 1500, 8000));
    let words = null;
    if (def.timing === "word") {
      // ritmo irregular, como um Whisper
      const parts = text.split(" ");
      const weights = parts.map((w, k) =>
        (w.length + 2) * (k === parts.length - 1 ? 2 : 1) * (k % 3 === 1 ? 0.7 : 1)
      );
      const total = weights.reduce((a, b) => a + b, 0);
      let acc = start;
      words = parts.map((w, k) => {
        const d = ((end - start) * weights[k]) / total;
        const word = { text: w, start_ms: Math.round(acc), end_ms: Math.round(acc + d) };
        acc += d;
        return word;
      });
      words[words.length - 1].end_ms = end;
      for (let k = 1; k < words.length; k++) {
        words[k].start_ms = Math.max(words[k].start_ms, words[k - 1].end_ms);
      }
    }
    return { i, start_ms: start, end_ms: end, text, words };
  });
  const source = def.timing === "word" ? "whisper" : "lrclib";
  return {
    version: 1,
    song_id: def.id,
    candidate_id: source + ":1",
    source,
    timing: def.timing,
    language: "pt",
    duration_ms: def.duration * 1000,
    offset_ms: 0,
    lines,
    meta: {
      track_name: def.title,
      artist_name: def.artist,
      album_name: null,
      provider_id: null,
      created_at: nowIso(),
    },
  };
}

// ------------------------------------------------------------------ servidor simulado

export function installMock(params) {
  const opts = {
    emptyQueue: params.get("mockq") === "empty",
    noLyrics: params.get("mocklyrics") === "none",
    broken: params.get("mockerr") === "1",
    prep: params.get("mockprep") === "1",
    otherOwner: params.get("mockowner") === "other",
    startAt: Number(params.get("mockstart")) || 0,
    startPaused: params.get("mockpaused") === "1",
    autoplay: params.get("autoplay") !== "0",
    countdown: Number(params.get("countdown")) || 8,
  };

  const songs = {};
  SONGS.forEach((def, i) => {
    const broken = opts.broken && i === 0;
    songs[def.id] = {
      id: def.id,
      youtube_id: def.id,
      title: def.title,
      artist: def.artist,
      original_title: `${def.artist} — ${def.title} (Clipe Oficial)`,
      channel: def.artist,
      duration_ms: def.duration * 1000,
      status: "ready",
      job_id: null,
      thumbnail: artUri(i),
      lyrics: opts.noLyrics && i === 0
        ? { source: null, timing: "none", candidate_id: null, candidates: 0 }
        : {
          source: def.timing === "word" ? "whisper" : "lrclib",
          timing: def.timing,
          candidate_id: "x:1",
          candidates: 1,
        },
      offset_ms: 0,
      media: {
        instrumental: broken ? "mock:broken" : `mock:${def.id}/instrumental`,
        vocals: broken ? "mock:broken" : `mock:${def.id}/vocals`,
        version: 1,
      },
      size_bytes: 11534336,
      added_by: "host",
      can_remove: true,
      created_at: nowIso(),
    };
  });

  let seq = 0;
  const makeItem = (songId, singer) => ({
    id: "q_" + ++seq,
    song_id: songId,
    singer,
    status: "pending",
    ready: true,
    song: songs[songId],
    job: null,
    added_by: "host",
    can_remove: true,
    added_at: nowIso(),
  });

  const queue = { revision: 1, current: null, items: [], history: [] };
  if (!opts.emptyQueue) {
    queue.items = [
      makeItem("mockCancao1", "Ana"),
      makeItem("mockLuzes01", "Bruno"),
      makeItem("mockDanca01", null),
    ];
  }
  if (opts.prep && queue.items[0]) {
    queue.items = queue.items.slice(0, 1); // só uma música, ainda processando
    queue.items[0].ready = false;
    queue.items[0].song = { ...queue.items[0].song, status: "processing", media: null };
    queue.items[0].job = { stage: "separating", progress: 0.42 };
  }

  const st = {
    revision: 1,
    status: "idle",
    queue_item_id: null,
    song_id: null,
    position_ms: 0,
    position_at: Date.now(),
    duration_ms: 0,
    volume: 0.8,
    guide_vocal: 0,
    offset_ms: 0,
    autoplay: opts.autoplay,
    up_next: null,
    stage: { connected: 1, audio_owner: opts.otherOwner ? OTHER_STAGE : null, unlocked: opts.otherOwner },
  };
  let upnextTimer = 0;
  let firstStart = true;

  const listeners = new Set();
  const mockSettings = { scoring_enabled: params.get("mockscore") !== "0" };
  const emit = (type, data) => {
    for (const es of listeners) es._dispatch(type, data);
  };

  const curPos = () => {
    let pos = st.position_ms;
    if (st.status === "playing") pos += Date.now() - st.position_at;
    return clamp(pos, 0, st.duration_ms || 0);
  };
  const setPos = (ms) => {
    st.position_ms = Math.round(clamp(ms, 0, st.duration_ms || 0));
    st.position_at = Date.now();
  };
  const playerState = () => {
    const cur = queue.current;
    return JSON.parse(JSON.stringify({
      ...st,
      song: cur ? { ...cur.song, offset_ms: st.offset_ms } : null,
      singer: cur ? cur.singer : null,
    }));
  };
  const pushState = () => {
    st.revision++;
    emit("player.state", playerState());
  };
  const pushQueue = () => {
    queue.revision++;
    emit("queue.updated", JSON.parse(JSON.stringify(queue)));
  };

  function startItem(item) {
    clearTimeout(upnextTimer);
    queue.items = queue.items.filter((it) => it !== item);
    item.status = "playing";
    queue.current = item;
    st.queue_item_id = item.id;
    st.song_id = item.song_id;
    st.duration_ms = item.song.duration_ms;
    st.offset_ms = item.song.offset_ms;
    st.up_next = null;
    setPos(0);
    st.status = st.stage.audio_owner ? "loading" : "waiting_gesture";
    pushQueue();
    pushState();
  }

  function finishCurrent(how) {
    if (!queue.current) return;
    queue.current.status = how;
    queue.history.unshift(queue.current);
    queue.history = queue.history.slice(0, 20);
    queue.current = null;
  }

  function advance(how, withCountdown) {
    clearTimeout(upnextTimer);
    finishCurrent(how);
    const next = queue.items.find((it) => it.ready);
    if (!next) {
      st.status = queue.history.length ? "ended" : "idle";
      st.queue_item_id = null;
      st.song_id = null;
      st.up_next = null;
      st.duration_ms = 0;
      setPos(0);
      pushQueue();
      pushState();
      return;
    }
    if (!withCountdown) return startItem(next);
    st.status = "up_next";
    st.queue_item_id = null;
    st.song_id = null;
    st.up_next = {
      queue_item_id: next.id,
      song: next.song,
      singer: next.singer,
      starts_at: st.autoplay ? Date.now() + opts.countdown * 1000 : null,
    };
    if (st.autoplay) upnextTimer = setTimeout(() => startItem(next), opts.countdown * 1000);
    pushQueue();
    pushState();
  }

  function startFromIdle() {
    const next = queue.items.find((it) => it.ready);
    if (next) startItem(next);
  }

  // ---- outro Palco simulado (modo espelho) e preparo simulado
  setInterval(() => {
    if (st.stage.audio_owner !== OTHER_STAGE) return;
    if (st.status === "loading") {
      st.status = opts.startPaused ? "paused" : "playing";
      setPos(firstStart ? opts.startAt * 1000 : 0);
      firstStart = false;
      pushState();
    } else if (st.status === "playing") {
      const pos = curPos();
      if (pos >= st.duration_ms) advance("done", true);
      else {emit("player.position", {
          queue_item_id: st.queue_item_id,
          position_ms: pos,
          position_at: Date.now(),
          playing: true,
        });}
    }
  }, 250);

  if (opts.prep) {
    const stages = ["downloading", "separating", "lyrics", "finalizing"];
    const timer = setInterval(() => {
      const item = queue.items[0];
      if (!item || item.ready) return clearInterval(timer);
      const p = round2(Math.min(1, item.job.progress + 0.02));
      item.job = { stage: stages[Math.min(3, Math.floor(p * 4))], progress: p };
      if (p >= 1) {
        item.ready = true;
        item.job = null;
        item.song = songs[item.song_id];
        pushQueue();
        if (st.status === "idle" || st.status === "ended") startFromIdle();
        return;
      }
      pushQueue();
    }, 500);
  }

  // estado inicial: há música pronta → espera o gesto (ou o outro Palco já toca)
  if (queue.items.some((it) => it.ready)) startFromIdle();

  // ---- comandos (§9)
  function command(body) {
    const v = body.value;
    switch (body.action) {
      case "play":
        if (!st.stage.audio_owner) {
          st.status = queue.current ? "waiting_gesture" : st.status;
          return { status: 409, code: "stage_not_connected", message: "Nenhum Palco está tocando o som." };
        }
        if (st.status === "paused") {
          st.status = "playing";
          st.position_at = Date.now();
        } else if (st.status === "up_next") {
          const next = queue.items.find((it) => it.id === st.up_next.queue_item_id);
          if (next) startItem(next);
          return null;
        } else if (st.status === "idle" || st.status === "ended") {
          startFromIdle();
          return null;
        }
        break;
      case "pause":
        if (st.status === "playing") {
          setPos(curPos());
          st.status = "paused";
        }
        break;
      case "toggle":
        return command({ action: st.status === "playing" ? "pause" : "play" });
      case "next":
        advance("skipped", false);
        return null;
      case "prev":
        if (curPos() > 3000 || !queue.history.length) setPos(0);
        else {
          const prev = queue.history.shift();
          if (queue.current) {
            queue.current.status = "pending";
            queue.items.unshift(queue.current);
            queue.current = null;
          }
          prev.status = "pending";
          queue.items.unshift(prev);
          startItem(prev);
          return null;
        }
        break;
      case "restart":
        setPos(0);
        break;
      case "seek":
        setPos(Number(v));
        break;
      case "seek_relative":
        setPos(curPos() + Number(v));
        break;
      case "volume":
        st.volume = round2(clamp(Number(v), 0, 1));
        break;
      case "volume_relative":
        st.volume = round2(clamp(st.volume + Number(v), 0, 1));
        break;
      case "guide_vocal":
        st.guide_vocal = round2(clamp(Number(v), 0, 1));
        break;
      case "guide_vocal_relative":
        st.guide_vocal = round2(clamp(st.guide_vocal + Number(v), 0, 1));
        break;
      case "offset":
      case "offset_relative": {
        const val = body.action === "offset" ? Number(v) : st.offset_ms + Number(v);
        st.offset_ms = clamp(Math.round(val), -10000, 10000);
        if (queue.current) queue.current.song.offset_ms = st.offset_ms;
        break;
      }
      case "autoplay":
        st.autoplay = !!v;
        if (st.status === "up_next") {
          clearTimeout(upnextTimer);
          st.up_next.starts_at = st.autoplay ? Date.now() + opts.countdown * 1000 : null;
          if (st.autoplay) {
            const id = st.up_next.queue_item_id;
            upnextTimer = setTimeout(() => {
              const it = queue.items.find((x) => x.id === id);
              if (it) startItem(it);
            }, opts.countdown * 1000);
          }
        }
        break;
      case "start": {
        const it = queue.items.find((x) => x.id === v);
        if (!it) {
          return { status: 404, code: "queue_item_not_found", message: "Item da fila não encontrado." };
        }
        finishCurrent("skipped");
        startItem(it);
        return null;
      }
      default:
        return { status: 400, code: "invalid_request", message: "Ação inválida." };
    }
    pushState();
    return null;
  }

  function report(body) {
    if (body.client_id !== st.stage.audio_owner || body.queue_item_id !== st.queue_item_id) return;
    switch (body.event) {
      case "loaded":
        if (st.status === "loading") {
          st.status = firstStart && opts.startPaused ? "paused" : "playing";
          setPos(firstStart ? opts.startAt * 1000 : 0);
          firstStart = false;
          pushState();
        }
        break;
      case "ended":
        advance("done", true);
        break;
      case "error":
        advance("skipped", false);
        break;
      default:
        st.position_ms = body.position_ms;
        st.position_at = Date.now();
        emit("player.position", {
          queue_item_id: st.queue_item_id,
          position_ms: body.position_ms,
          position_at: st.position_at,
          playing: !!body.playing,
        });
    }
  }

  const ok = (data, status = 200) => ({ status, data });
  const fail = (status, code, message, details = {}) => ({
    status,
    data: { error: { code, message, details } },
  });

  setTransport(async (method, path, body) => {
    await new Promise((r) => setTimeout(r, 15)); // latência local
    const url = new URL(path, location.origin);
    const p = url.pathname;
    let m;
    if (method === "GET" && p === "/api/health") return ok({ ok: true, version: "mock" });
    if (method === "GET" && p === "/api/player") return ok(playerState());
    if (method === "GET" && p === "/api/queue") return ok(JSON.parse(JSON.stringify(queue)));
    if (method === "GET" && (m = p.match(/^\/api\/songs\/([A-Za-z0-9_-]{11})\/lyrics$/))) {
      const def = SONGS.find((s) => s.id === m[1]);
      if (!def) return fail(404, "song_not_found", "Música não encontrada.", { song_id: m[1] });
      return ok(lyricsDoc(def, opts));
    }
    if (method === "GET" && (m = p.match(/^\/api\/songs\/([A-Za-z0-9_-]{11})\/pitch$/))) {
      const def = SONGS.find((s) => s.id === m[1]);
      if (!def) return fail(404, "song_not_found", "Música não encontrada.", { song_id: m[1] });
      return ok({
        song_id: def.id,
        notes: params.get("mockpitch") === "none" ? [] : mockNotes(def.duration),
      });
    }
    if (method === "PATCH" && p === "/api/settings") {
      Object.assign(mockSettings, body || {});
      emit("settings.updated", { ...mockSettings });
      return ok({ ...mockSettings });
    }
    if (method === "POST" && p === "/api/stage/claim") {
      st.stage.audio_owner = body.client_id;
      st.stage.unlocked = true;
      if (st.status === "waiting_gesture") st.status = "loading";
      pushState();
      return ok(playerState());
    }
    if (method === "POST" && p === "/api/player/command") {
      const err = command(body || {});
      if (err) return fail(err.status, err.code, err.message);
      return ok(playerState());
    }
    if (method === "POST" && p === "/api/player/report") {
      report(body || {});
      return { status: 204, data: null };
    }
    return fail(501, "not_implemented", "Rota não simulada no mock.", { feature: p });
  });

  class FakeEventSource {
    constructor() {
      this.readyState = 0;
      this.handlers = {};
      listeners.add(this);
      setTimeout(() => {
        this.readyState = 1;
        this._dispatch("open", null);
        this._dispatch("hello", {
          server_time: Date.now(),
          client_id: "mock",
          player: playerState(),
          queue: JSON.parse(JSON.stringify(queue)),
          jobs: [],
          settings: {
            ...mockSettings,
            countdown_s: opts.countdown,
            autoplay: st.autoplay,
            guide_vocal_default: 0,
            volume: st.volume,
            separation_model: "htdemucs_ft",
            whisper_model: "large-v3-turbo",
            long_video_warn_s: 900,
            theme: "system",
          },
          system: { device: "cuda", gpu_warning: null, model_download: null },
        });
      }, 30);
    }
    addEventListener(type, fn) {
      (this.handlers[type] ||= []).push(fn);
    }
    close() {
      this.readyState = 2;
      listeners.delete(this);
    }
    _dispatch(type, data) {
      const ev = { type, data: data == null ? "" : JSON.stringify(data) };
      for (const fn of this.handlers[type] || []) fn(ev);
    }
  }
  setEventSourceFactory(() => new FakeEventSource());

  return { fetchBuffer: (url, ctx) => synthesize(url, ctx, songs) };
}

// ------------------------------------------------------------------ áudio sintético

const CHORDS = [220, 174.61, 196, 146.83]; // Lá, Fá, Sol, Ré
const SCALE = [440, 493.88, 554.37, 587.33, 659.25, 739.99];

async function synthesize(url, ctx, songs) {
  await new Promise((r) => setTimeout(r, 300)); // simula o download/decodificação
  if (url === "mock:broken") {
    const song = songs.mockCancao1;
    const err = new Error(`O arquivo de áudio de “${song.title}” não pôde ser decodificado.`);
    err.code = "decode_failed";
    throw err;
  }
  const [, id, stem] = url.match(/^mock:([^/]+)\/(\w+)$/) || [];
  const def = SONGS.find((s) => s.id === id);
  const rate = ctx.sampleRate;
  const len = Math.ceil(def.duration * rate);
  const data = new Float32Array(len);
  if (stem === "instrumental") {
    for (let n = 0; n < len; n++) {
      const t = n / rate;
      const root = CHORDS[Math.floor(t / 2) % CHORDS.length];
      const env = Math.min(1, (t % 2) * 8) * (1 - 0.3 * ((t % 2) / 2));
      let s = 0.05 * env * (Math.sin(2 * Math.PI * root * t) + 0.6 * Math.sin(2 * Math.PI * root * 1.5 * t) +
        0.4 * Math.sin(2 * Math.PI * root * 2 * t));
      const beat = t % 0.5;
      if (beat < 0.12) {
        s += 0.25 * Math.exp(-beat * 30) * Math.sin(2 * Math.PI * (50 + 60 * Math.exp(-beat * 40)) * beat);
      }
      const off = (t + 0.25) % 0.5;
      if (off < 0.03) s += 0.03 * (Math.random() * 2 - 1) * (1 - off / 0.03);
      data[n] = s;
    }
  } else {
    const prepared = prepareLyrics(lyricsDoc(def, {}));
    let k = 0;
    for (const line of prepared.lines) {
      for (const w of line.words) {
        const f = SCALE[k++ % SCALE.length];
        const a = Math.floor((w.start_ms / 1000) * rate);
        const b = Math.min(len, Math.floor((w.end_ms / 1000) * rate));
        for (let n = a; n < b; n++) {
          const t = (n - a) / rate;
          const d = (b - a) / rate;
          const env = Math.min(1, t * 40, (d - t) * 20);
          data[n] += 0.18 * env * Math.sin(2 * Math.PI * f * (n / rate));
        }
      }
    }
  }
  const buf = ctx.createBuffer(2, len, rate);
  buf.copyToChannel(data, 0);
  buf.copyToChannel(data, 1);
  return buf;
}

/** Melodia sintética (escala de Dó em C4–C5, notas de 0,6 s com pausa de 0,2 s) — formato de `GET …/pitch`. */
function mockNotes(durationS) {
  const scale = [60, 62, 64, 65, 67, 69, 71, 72];
  const names = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"];
  const notes = [];
  for (let t = 2, i = 0; t + 0.6 < durationS; t += 0.8, i++) {
    const k = i % scale.length;
    notes.push({ start: t, end: t + 0.6, midi: scale[k], note: names[k] });
  }
  return notes;
}
