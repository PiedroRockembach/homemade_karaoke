/**
 * Entrada do Palco (/stage) — ARCHITECTURE §4–5, contrato §9–10, DESIGN §5.3.
 *
 * Fluxo: SSE `role=stage` → "Pressione Enter" → gesto destrava o AudioContext → `POST /api/stage/claim`
 * → para cada `player.state`: carrega mídia + letra, `report loaded`, aplica play/pause/seek/volume/voz guia
 * → `report tick` a cada 500 ms enquanto toca → teclas viram `POST /api/player/command`.
 *
 * - O Palco dono do áudio (audio owner) usa o relógio do AudioEngine para letra e barra.
 * - Palco espelho (outro client_id é o owner): não toca; letra pelo `player.position` do servidor.
 * - `?mock=1` instala um servidor simulado (mock.js) que segue o contrato — isolado, fácil de remover.
 *
 * Dono: frontend-engineer (T-007).
 */

import { api } from "../shared/api.js";
import { connectEvents, getClientId } from "../shared/events.js";
import { fmtEta, jobStageLabel } from "../shared/format.js";
import { applyEvent, createStore, extrapolatePosition } from "../shared/state.js";
import { AudioEngine } from "./audio-engine.js";
import { Hud } from "./hud.js";
import { createKeyHandler } from "./keys.js";
import { LyricsRenderer } from "./lyrics-renderer.js";
import { GuestQr } from "./guest-qr.js";
import { Screens } from "./screens.js";
import { PitchRoll } from "./pitch-roll.js";
import { MicPitch } from "./mic-pitch.js";
import { checkPitchHit, noteAt, Scorer } from "./scorer.js";
import { PitchSmoother } from "./pitch-smooth.js";
import { nextOctaveShift, octaveShiftLabel, parseOctaveShift } from "./pitch-roll.js";

const TICK_MS = 500; // report tick (contrato §9: 2 Hz)
const SEEK_TOLERANCE_S = 1.0; // diferença servidor × engine que caracteriza um seek
const ERROR_SKIP_MS = 5000; // "Pulando para a próxima em 5 s…"
const CLAIM_RETRY_MS = 3000;
const OFFLINE_GRACE_MS = 1500;
const CURSOR_IDLE_MS = 3000;
const SONG_STATUSES = new Set(["loading", "playing", "paused"]);
const OCTAVE_KEY = "karaoke.stage.octaveShift"; // deslocamento da régua, por Palco (US-27)
const FINAL_CARD_MS = 4000; // cartão de pontuação final (DESIGN §13.2)
const FINAL_CARD_MIN_MS = 1500; // menos que isso de contagem: não vale mostrar o cartão
const MIC_KEEP_STATUSES = new Set(["loading", "playing", "paused", "up_next"]); // microfone segue aberto entre faixas

const mediaKey = (song) => song.id + "@" + ((song.media && song.media.version) || 0);

class Stage {
  constructor({ fetchBuffer } = {}) {
    this.root = document.getElementById("stage");
    this.clientId = getClientId();
    this.store = createStore({ clientId: this.clientId });
    this.engine = new AudioEngine({ fetchBuffer });
    this.screens = new Screens(this.root);
    this.guestQr = new GuestQr(this.root);
    this.hud = new Hud(this.root.querySelector(".hud"));
    this.renderer = new LyricsRenderer(this.root.querySelector("[data-lyrics]"));
    this.help = this.root.querySelector("[data-help]");

    this.pitchRoll = new PitchRoll(
      this.root.querySelector("[data-pitch-canvas]"),
      this.root.querySelector("[data-pitch-box]"),
    );
    this.pitchNote = this.root.querySelector("[data-pitch-note]");
    this.mic = new MicPitch();
    this.scorer = new Scorer(this.root);
    this.pitchCache = new Map();
    /** notas da faixa atual: status "loading" | "ok" | "none" (sem notas ou falha — nunca bloqueia o áudio) */
    this.pitch = { itemId: null, songId: null, status: "none", notes: [] };
    this.smoother = new PitchSmoother();
    this.octaveShift = 0;
    try {
      this.octaveShift = parseOctaveShift(localStorage.getItem(OCTAVE_KEY));
    } catch { /* sem armazenamento: começa na oitava original */ }
    this.pitchRoll.setOctaveShift(this.octaveShift);
    this.lastFrameAt = performance.now();
    this.scoringOptimistic = null; // {v, t}: valor do atalho S até o servidor confirmar
    this.lastScoringOn = false;
    this.scoringGen = 0; // só a resposta do último S limpa o valor otimista
    /** cartão final pendente/visível: {itemId, points, until: number|null} */
    this.finalCard = null;

    this.unlocked = false;
    this.isOwner = false;
    this.mirror = false;
    this.claiming = false;
    this.claimRetryAt = 0;
    this.lastRev = null;
    this.gestureHoldRev = null; // revisão em que já pedimos `pause` por falta de gesto (evita repetir)
    this.resumeItem = null; // item pausado por falta de gesto: retoma (play) quando o áudio carregar
    /** @type {null|{itemId:string, songId:string, song:any, key:string, status:string, error:any, ended:boolean}} */
    this.track = null;
    this.lyrics = { songId: null, status: "none" };
    this.lyricsCache = new Map();
    this.errorState = null; // {itemId, deadline, waiting}
    this.upnextKey = "";
    this.upnextTotal = 8000;
    this.prepSamples = null;
    this.wakeLock = null;
    this.offlineTimer = 0;

    this.engine.onEnded = () => this.onEngineEnded();
  }

  start() {
    connectEvents({
      role: "stage",
      clientId: this.clientId,
      onEvent: (type, data) => this.onEvent(type, data),
      onStatus: (status) => this.onConnection(status),
    });

    const onKey = createKeyHandler({
      player: () => this.store.get().player,
      scene: () => this.screens.current,
      helpOpen: () => !this.help.hidden,
      setHelp: (open) => this.setHelp(open),
      enter: () => this.enter(),
      space: () => this.space(),
      command: (action, value) => this.command(action, value),
      hud: (...args) => this.hud.show(...args),
      toggleFullscreen: () => this.toggleFullscreen(),
      toggleScoring: () => this.toggleScoring(),
      cycleOctave: () => this.cycleOctave(),
    });
    document.addEventListener("keydown", onKey);

    this.root.addEventListener("click", () => {
      if (this.screens.current === "start" || this.mirror) this.unlockAndClaim();
    });
    this.setupCursorHiding();
    document.addEventListener("visibilitychange", () => this.updateWakeLock());
    addEventListener("pagehide", () => this.mic.stop());

    setInterval(() => {
      if (this.isOwner && this.engine.playing) this.report("tick");
    }, TICK_MS);

    this.sync();
    this.root.focus({ preventScroll: true });
    const frame = () => {
      this.frame();
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  // ---------------------------------------------------------------- eventos do servidor

  onEvent(type, data) {
    applyEvent(this.store, type, data);
    if (type === "hello" && !data.player) this.fetchSnapshot();
    if (type === "lyrics.updated") {
      this.lyricsCache.delete(data.song_id);
      if (this.track && this.track.songId === data.song_id) this.loadLyrics(data.song_id);
    }
    if (type === "song.updated" && this.track && this.track.songId === data.id) {
      // offset/título mudaram; a mídia só muda com nova versão (novo key → recarrega).
      this.track.song = { ...this.track.song, ...data };
    }
    this.sync();
  }

  /** hello sem snapshot (backend parcial): busca player e fila por REST, ignorando falhas. */
  fetchSnapshot() {
    api.get("/api/player").then((p) => p && applyEvent(this.store, "player.state", p)).catch(() => {})
      .finally(() => this.sync());
    api.get("/api/queue").then((q) => q && applyEvent(this.store, "queue.updated", q)).catch(() => {})
      .finally(() => this.sync());
  }

  onConnection(status) {
    this.store.set({ connection: status });
    clearTimeout(this.offlineTimer);
    if (status === "open") this.screens.setFlags({ offline: false });
    else {
      this.offlineTimer = setTimeout(() => {
        if (this.store.get().connection !== "open") this.screens.setFlags({ offline: true });
      }, OFFLINE_GRACE_MS);
    }
  }

  // ---------------------------------------------------------------- reconciliação

  /** Reconcilia áudio, letra e tela com o PlayerState atual. Idempotente. */
  sync() {
    const s = this.store.get();
    const p = s.player;
    const owner = (p && p.stage && p.stage.audio_owner) || null;
    this.isOwner = !!owner && owner === this.clientId;
    this.mirror = !!owner && !this.isOwner;
    const status = (p && p.status) || "idle";
    const revChanged = !!p && p.revision !== this.lastRev;

    if (this.unlocked && p && !owner && !this.claiming && performance.now() >= this.claimRetryAt) {
      this.claim(false);
    }
    if (this.isOwner && !this.unlocked) this.holdForGesture(p, status);
    else if (!this.isOwner) this.resumeItem = null;

    if (p && p.song_id && SONG_STATUSES.has(status)) this.ensureTrack(p);
    else this.dropTrack();

    if (status === "up_next" && p.up_next && p.up_next.song) this.preload(p.up_next.song);

    if (this.isOwner) this.applyTransport(p, revChanged);
    else if (this.engine.playing) this.engine.stop();

    if (p) {
      this.engine.setVolume(p.volume ?? 0.8);
      this.engine.setGuide(p.guide_vocal ?? 0);
    }
    if (p) this.lastRev = p.revision;
    this.updateWakeLock();
    this.renderScene();
  }

  ensureTrack(p) {
    const song = p.song || (this.track && this.track.song);
    if (!this.track || this.track.itemId !== p.queue_item_id) {
      this.engine.stop();
      this.track = {
        itemId: p.queue_item_id,
        songId: p.song_id,
        song,
        key: song ? mediaKey(song) : p.song_id,
        status: "idle",
        error: null,
        ended: false,
      };
      this.errorState = null;
      this.loadLyrics(p.song_id);
    } else if (song) {
      this.track.song = song;
      if (mediaKey(song) !== this.track.key && this.track.status !== "loading") {
        this.track.key = mediaKey(song);
        this.track.status = "idle";
      }
    }
    if (this.isOwner && this.unlocked && this.track.status === "idle") this.loadAudio(this.track);
  }

  dropTrack() {
    if (!this.track) return;
    this.engine.clearTrack();
    this.track = null;
    this.errorState = null;
    this.renderer.setLyrics(null);
    this.lyrics = { songId: null, status: "none" };
  }

  loadAudio(track) {
    const song = track.song;
    if (!song || !song.media || !song.media.instrumental) {
      this.failTrack(track, { message: "A música ainda não tem áudio pronto." });
      return;
    }
    track.status = "loading";
    this.engine.load(track.key, song.media)
      .then(() => {
        if (this.track !== track) return null;
        return this.engine.setTrack(track.key, song.media).then(() => {
          if (this.track !== track) return;
          // Música já em andamento (F5, troca de Palco): parte da posição do servidor, não de 0 (review-1 H1).
          const p = this.store.get().player;
          const resuming = !!p && p.queue_item_id === track.itemId &&
            (p.status === "playing" || p.status === "paused");
          if (resuming) this.engine.seek(extrapolatePosition(p, this.store.get().serverSkewMs) / 1000);
          track.status = "ready";
          this.report("loaded");
          if (this.resumeItem === track.itemId) {
            this.resumeItem = null;
            if (p && p.status === "paused") this.command("play");
          }
          this.sync();
        });
      })
      .catch((err) => {
        if (this.track === track) this.failTrack(track, err);
      });
  }

  failTrack(track, err) {
    track.status = "error";
    track.error = {
      code: (err && err.code) || "decode_failed",
      message: (err && err.message) || "O áudio desta música não pôde ser carregado.",
    };
    this.errorState = { itemId: track.itemId, deadline: performance.now() + ERROR_SKIP_MS, waiting: false };
    this.renderScene();
  }

  preload(song) {
    if (!this.isOwner || !this.unlocked || !song.media || !song.media.instrumental) return;
    this.engine.load(mediaKey(song), song.media).catch(() => {});
    this.getLyrics(song.id).catch(() => {});
  }

  /**
   * Palco dono do áudio mas sem gesto (ex.: F5 — o client_id do sessionStorage mantém a posse, mas o
   * AudioContext novo está travado): não dá para tocar. Pausa no servidor preservando a posição, para o
   * Controle não mostrar "Tocando" em silêncio; a tela fica em "Pressione Enter" e, após o gesto, retoma
   * do mesmo ponto quando o áudio carregar (review-1 M3).
   */
  holdForGesture(p, status) {
    if (!p || (status !== "playing" && status !== "loading")) return;
    if (this.gestureHoldRev === p.revision) return;
    this.gestureHoldRev = p.revision;
    this.resumeItem = p.queue_item_id;
    this.command("pause");
  }

  applyTransport(p, revChanged) {
    const t = this.track;
    if (!p || !t || t.status !== "ready" || t.itemId !== p.queue_item_id) return;
    const expected = extrapolatePosition(p, this.store.get().serverSkewMs) / 1000;
    const far = Math.abs(expected - this.engine.position) > SEEK_TOLERANCE_S;
    if (t.ended && revChanged && expected < this.engine.duration - 0.5) t.ended = false;

    if (p.status === "playing") {
      if (!this.engine.playing) {
        if (t.ended) return; // aguardando o servidor reagir ao `ended`
        this.engine.play(expected);
        this.renderer.reset();
        this.report("started");
      } else if (revChanged && far) {
        this.engine.seek(expected);
        this.renderer.reset();
        this.report("seeked");
      }
    } else if (p.status === "paused") {
      if (this.engine.playing) {
        this.engine.pause();
        this.report("paused");
      } else if (revChanged && far) {
        this.engine.seek(expected);
        this.renderer.reset();
        this.report("seeked");
      }
    }
  }

  onEngineEnded() {
    if (!this.track) return;
    this.track.ended = true;
    if (this.mic.active && this.scoringOn() && this.pitch.status === "ok") {
      // Exibido quando a cena seguinte ("A seguir" ou espera) aparecer — ver `syncFinalCard`.
      this.finalCard = { itemId: this.track.itemId, points: this.scorer.score, until: null };
    }
    this.report("ended");
  }

  // ---------------------------------------------------------------- letra

  getLyrics(songId) {
    if (!this.lyricsCache.has(songId)) {
      const p = api.get(`/api/songs/${encodeURIComponent(songId)}/lyrics`);
      p.catch(() => this.lyricsCache.delete(songId));
      this.lyricsCache.set(songId, p);
      if (this.lyricsCache.size > 4) this.lyricsCache.delete(this.lyricsCache.keys().next().value);
    }
    return this.lyricsCache.get(songId);
  }

  loadLyrics(songId) {
    this.lyrics = { songId, status: "loading" };
    this.renderer.setLyrics(null);
    this.getLyrics(songId)
      .then((doc) => {
        if (this.lyrics.songId !== songId) return;
        this.lyrics.status = this.renderer.setLyrics(doc) ? "ok" : "none";
        this.renderScene();
      })
      .catch(() => {
        if (this.lyrics.songId !== songId) return;
        this.lyrics.status = "error";
        this.renderScene();
      });
  }

  // ---------------------------------------------------------------- telas

  pickScene(p, queue) {
    const status = (p && p.status) || "idle";
    if (!this.unlocked && !this.mirror) return "start";
    if (this.isOwner && this.track && this.track.status === "error" && SONG_STATUSES.has(status)) {
      return "error";
    }
    switch (status) {
      case "waiting_gesture":
        return this.mirror ? "idle" : "loading";
      case "loading":
        return "loading";
      case "playing":
      case "paused":
        return "play";
      case "up_next":
        return "upnext";
      default: {
        const first = queue && queue.items && queue.items[0];
        return first && !first.ready ? "prep" : "idle";
      }
    }
  }

  renderScene() {
    const s = this.store.get();
    const p = s.player;
    const queue = s.queue;
    const scene = this.pickScene(p, queue);
    const status = (p && p.status) || "idle";
    const song = (p && p.song) || (this.track && this.track.song) || null;
    const nextItem = (queue && queue.items && queue.items[0]) || null;

    if (scene === "start") this.screens.renderStart(queue, status === "playing" || status === "paused");
    if (scene === "play") this.screens.renderPlayMeta(song, p && p.singer, nextItem);
    if (scene === "loading") {
      const first = (queue && (queue.current || (queue.items && queue.items[0]))) || null;
      const loadingSong = song || (first && first.song);
      this.screens.renderLoading(loadingSong, (p && p.singer) || (first && first.singer));
    }
    if (scene === "upnext") {
      const up = p.up_next || {};
      const manual = up.starts_at == null;
      const key = (up.queue_item_id || "") + ":" + manual;
      if (key !== this.upnextKey) {
        this.upnextKey = key;
        this.screens.renderUpNext(up, manual);
        const fromSettings = s.settings && s.settings.countdown_s;
        const remaining = manual ? 0 : up.starts_at - (Date.now() + s.serverSkewMs);
        this.upnextTotal = fromSettings ? fromSettings * 1000 : Math.max(1000, remaining);
      }
    } else this.upnextKey = "";
    if (scene === "prep") this.renderPrep(s, nextItem);
    else this.prepSamples = null;
    if (scene === "error") this.screens.renderError(this.track.error.message, this.errorSecondsLeft());

    let bg = null;
    if (scene === "play" || scene === "loading" || scene === "error") bg = song && song.thumbnail;
    if (scene === "upnext") bg = p.up_next && p.up_next.song && p.up_next.song.thumbnail;
    this.screens.setBackground(bg || null);

    const lyricsMissing = this.lyrics.status === "none" || this.lyrics.status === "error";
    this.screens.setFlags({
      paused: status === "paused",
      mirror: this.mirror,
      nolyrics: scene === "play" && lyricsMissing,
    });
    this.screens.show(scene);
    this.guestQr.update(s.guestState, status, scene);
    this.syncScoring(scene);
    this.syncFinalCard(scene);
  }

  /**
   * Cartão final (US-19 AC-19.6, review-1 M1): aparece quando a música acabou e a cena vira "A seguir"
   * (faixa compacta no topo, por min(4 s, contagem − 1 s)) ou espera sem próxima música (tela cheia, 4 s).
   * Some ao começar outra faixa ("começar já" incluso), ao desligar a pontuação ou se o fim foi desfeito (seek).
   */
  syncFinalCard(scene) {
    const f = this.finalCard;
    if (!f) return;
    const now = performance.now();
    const undone = this.track && this.track.itemId === f.itemId && !this.track.ended;
    const waitingEnd = scene === "play" && this.track && this.track.itemId === f.itemId;
    if (!this.scoringOn() || undone || (f.until != null && now >= f.until)) return this.closeFinalCard();
    if (waitingEnd) return; // servidor ainda não reagiu ao `ended`
    if (scene !== "upnext" && scene !== "idle") return this.closeFinalCard();
    if (f.until != null) return;
    let dur = FINAL_CARD_MS;
    const p = this.store.get().player;
    if (scene === "upnext" && p && p.up_next && p.up_next.starts_at != null) {
      dur = Math.min(
        FINAL_CARD_MS,
        p.up_next.starts_at - (Date.now() + this.store.get().serverSkewMs) - 1000,
      );
    }
    if (dur < FINAL_CARD_MIN_MS) return this.closeFinalCard();
    f.until = now + dur;
    this.screens.announce(this.scorer.showFinal(f.points, scene === "upnext"));
    f.timer = setTimeout(() => this.renderScene(), dur + 20);
  }

  closeFinalCard() {
    if (this.finalCard) clearTimeout(this.finalCard.timer);
    this.finalCard = null;
    this.scorer.hideFinal();
  }

  // ---------------------------------------------------------------- pontuação e guia de notas (US-18/19/20)

  /** `settings.scoring_enabled` (padrão ligado); o atalho S vale de imediato, antes da resposta do PATCH. */
  scoringOn() {
    const o = this.scoringOptimistic;
    if (o) return o.v; // vale até a resposta do último S (sem prazo)
    const st = this.store.get().settings;
    return !(st && st.scoring_enabled === false);
  }

  /** Reconcilia régua, placar, aviso e microfone com setting + cena + notas. Idempotente. */
  syncScoring(scene) {
    const on = this.scoringOn();
    const t = this.track;
    if (on && !this.lastScoringOn) this.mic.error = null; // religou (S ou Controle): tenta o microfone de novo
    this.lastScoringOn = on;
    if (on && t && this.pitch.itemId !== t.itemId) this.loadPitch(t);
    if (!t && this.pitch.itemId) this.pitch = { itemId: null, songId: null, status: "none", notes: [] };

    const hasNotes = on && !!t && this.pitch.itemId === t.itemId && this.pitch.status === "ok";
    const scoring = hasNotes && this.isOwner && this.unlocked;
    const rollOn = hasNotes && scene === "play";
    this.pitchRoll.setVisible(rollOn);
    this.root.classList.toggle("scoring-on", rollOn); // régua maior e letra menor (FIX-001)
    this.scorer.setVisible(scoring && scene === "play" && !this.mic.error);

    // Abre numa faixa com notas; depois segue aberto entre faixas (A seguir/carregando, review-1 L).
    // Fecha ao desligar, perder a posse, ficar em espera ou sair da página.
    const status = this.store.get().player?.status;
    const keep = on && this.isOwner && this.unlocked && MIC_KEEP_STATUSES.has(status);
    if (scoring && SONG_STATUSES.has(status)) {
      if (!this.mic.active && !this.mic.pending && !this.mic.error) {
        this.mic.start().then(() => this.renderScene());
      }
    } else if (!keep && (this.mic.active || this.mic.pending)) {
      this.mic.stop();
      this.smoother.reset();
    }

    let note = "";
    if (on && scene === "play" && t && this.pitch.itemId === t.itemId && this.pitch.status === "none") {
      note = "Notas indisponíveis para esta faixa";
    } else if (scoring && scene === "play" && this.mic.error) {
      note = this.mic.error === "denied"
        ? "Permita o microfone no navegador para pontuar"
        : "Microfone indisponível neste navegador — a pontuação fica desligada";
    }
    this.pitchNote.textContent = note;
    this.pitchNote.hidden = !note;
  }

  /** Notas por `mediaKey` (nova preparação da música = nova versão = busca de novo). */
  getPitch(songId, key) {
    if (!this.pitchCache.has(key)) {
      const p = api.get(`/api/songs/${encodeURIComponent(songId)}/pitch`);
      p.catch(() => this.pitchCache.delete(key));
      this.pitchCache.set(key, p);
      if (this.pitchCache.size > 4) this.pitchCache.delete(this.pitchCache.keys().next().value);
    }
    return this.pitchCache.get(key);
  }

  loadPitch(track) {
    const { itemId, songId, key } = track;
    this.pitch = { itemId, songId, status: "loading", notes: [] };
    this.pitchRoll.setNotes([]);
    this.scorer.reset([]);
    this.getPitch(songId, key)
      .then((doc) => {
        const notes = ((doc && doc.notes) || [])
          .filter((n) => Number.isFinite(n.start) && Number.isFinite(n.end) && n.end > n.start && n.midi > 0)
          .sort((a, b) => a.start - b.start);
        return notes;
      })
      .catch(() => [])
      .then((notes) => {
        if (this.pitch.itemId !== itemId) return;
        this.pitch = { itemId, songId, status: notes.length ? "ok" : "none", notes };
        this.pitchRoll.setNotes(notes);
        this.scorer.reset(notes);
        this.renderScene();
      });
  }

  /** Atalho S: alterna na hora (HUD + Palco) e grava `PATCH /api/settings` — o SSE avisa os outros Palcos. */
  toggleScoring() {
    const next = !this.scoringOn();
    const gen = ++this.scoringGen;
    this.scoringOptimistic = { v: next, t: performance.now() };
    this.hud.show("target", next ? "Pontuação: Ativada" : "Pontuação: Desativada");
    this.renderScene();
    api.patch("/api/settings", { scoring_enabled: next })
      .then((st) => {
        if (st && gen === this.scoringGen) this.store.set({ settings: st });
      })
      .catch((e) => {
        if (gen === this.scoringGen) this.hud.show("alert", "Não deu para salvar a pontuação", e.message);
      })
      .finally(() => {
        if (gen !== this.scoringGen) return; // outro S em voo: ele decide
        this.scoringOptimistic = null;
        this.renderScene();
      });
  }

  /** Tecla O: régua 0 → −1 → +1 → 0 oitava; vale para as próximas músicas (localStorage, por Palco). */
  cycleOctave() {
    this.octaveShift = nextOctaveShift(this.octaveShift);
    this.pitchRoll.setOctaveShift(this.octaveShift);
    try {
      localStorage.setItem(OCTAVE_KEY, String(this.octaveShift));
    } catch { /* modo privado etc.: vale só nesta sessão */ }
    this.hud.show("music", octaveShiftLabel(this.octaveShift));
  }

  /**
   * Por quadro, na cena "play": leitura do microfone (a cada quadro), suavização por tempo, acerto contra
   * a nota na oitava da régua, placar e régua. O indicador mostra a altura real (sem dobra de oitava).
   */
  framePitch(posMs, latencyMs) {
    const now = performance.now();
    const dt = Math.min(0.1, (now - this.lastFrameAt) / 1000);
    this.lastFrameAt = now;
    if (this.pitchRoll.box.hidden) return;
    const timeS = (posMs - latencyMs) / 1000;
    const target = noteAt(this.pitch.notes, timeS);
    const targetMidi = target ? target.midi + 12 * this.octaveShift : null;
    const voice = this.mic.active ? this.smoother.update(this.mic.read(), now) : null;
    if (!voice) this.smoother.reset();
    const sung = voice && voice.voiced ? voice.midi : null; // só voz confiável pontua (AC-26.2)
    let hit = checkPitchHit(targetMidi, sung);
    if (this.mic.active && this.engine.playing) hit = this.scorer.tick(targetMidi, sung, dt, timeS);
    this.pitchRoll.render(timeS, voice ? voice.midi : null, target, hit, voice ? voice.alpha : 0);
  }

  renderPrep(s, item) {
    const job = item.job || {};
    const fullJob = Object.values(s.jobs || {}).find((j) => j.song_id === item.song_id) || null;
    const progress = job.progress ?? (fullJob && fullJob.progress) ?? null;
    const label = jobStageLabel(job.stage || (fullJob && fullJob.stage), fullJob && fullJob.lyrics_source);
    // ETA simples pela taxa de progresso observada desde que a tela apareceu.
    const now = performance.now();
    if (!this.prepSamples || this.prepSamples.itemId !== item.id) {
      this.prepSamples = { itemId: item.id, t0: now, p0: progress };
    }
    let eta = "";
    const { t0, p0 } = this.prepSamples;
    if (progress != null && p0 != null && progress > p0 && now - t0 > 4000) {
      const rate = (progress - p0) / ((now - t0) / 1000);
      eta = fmtEta((1 - progress) / rate);
    }
    this.screens.renderPrep(item.song, label, progress, eta);
  }

  errorSecondsLeft() {
    const e = this.errorState;
    if (!e || e.waiting) return null;
    return Math.max(0, Math.ceil((e.deadline - performance.now()) / 1000));
  }

  /** Atualizações por quadro: letra/barra (relógio do áudio), anel do "A seguir", contagem do erro. */
  frame() {
    const scene = this.screens.current;
    const s = this.store.get();
    const p = s.player;
    if (scene === "play" && p) {
      let posMs;
      let durMs;
      let latencyMs = 0;
      if (this.isOwner && this.track && this.track.status === "ready") {
        posMs = this.engine.position * 1000;
        durMs = this.engine.duration * 1000;
        latencyMs = this.engine.outputLatencySec * 1000;
      } else {
        posMs = extrapolatePosition(p, s.serverSkewMs);
        durMs = p.duration_ms || 0;
      }
      this.screens.updateProgress(posMs, durMs || p.duration_ms || 0);
      if (this.lyrics.status === "ok") this.renderer.render(posMs - latencyMs - (p.offset_ms || 0));
      this.framePitch(posMs, latencyMs);
    } else if (scene === "upnext" && p && p.up_next && p.up_next.starts_at != null) {
      this.screens.updateRing(p.up_next.starts_at - (Date.now() + s.serverSkewMs), this.upnextTotal);
    } else if (scene === "error" && this.errorState && !this.errorState.waiting) {
      const left = this.errorSecondsLeft();
      this.screens.renderError(this.track.error.message, left);
      if (left <= 0) this.skipFromError();
    }
  }

  // ---------------------------------------------------------------- ações

  /** Enter: começar (gesto/claim) · tocar aqui (espelho) · começar já (A seguir) · pular (erro). */
  enter() {
    const scene = this.screens.current;
    if (!this.unlocked || this.mirror) {
      this.unlockAndClaim();
      return;
    }
    if (scene === "upnext") {
      this.command("play");
      this.hud.show("play", "Começando agora");
    } else if (scene === "error") {
      this.skipFromError();
    }
  }

  /** Espaço com significado próprio da cena; true = tratado aqui. */
  space() {
    const scene = this.screens.current;
    if (scene === "start") return true;
    if (scene === "error" && this.errorState) {
      this.errorState.waiting = true;
      this.screens.renderError(this.track.error.message, null);
      return true;
    }
    return false;
  }

  unlockAndClaim() {
    const wasMirror = this.mirror;
    // unlock() cria/resume o AudioContext de forma síncrona dentro do gesto.
    this.engine.unlock()
      .then((ok) => {
        this.unlocked = ok;
        if (!ok) {
          this.hud.show("alert", "O navegador bloqueou o som", "pressione Enter de novo");
          return;
        }
        return this.claim(true, wasMirror);
      })
      .catch(() => this.hud.show("alert", "Não foi possível iniciar o áudio neste navegador"));
  }

  async claim(fromGesture, wasMirror = false) {
    this.claiming = true;
    this.claimRetryAt = performance.now() + CLAIM_RETRY_MS;
    try {
      const st = await api.post("/api/stage/claim", { client_id: this.clientId });
      if (st && typeof st.revision === "number") applyEvent(this.store, "player.state", st);
      if (fromGesture && wasMirror) this.hud.show("vol", "Som neste Palco");
    } catch (e) {
      if (fromGesture) this.hud.show("alert", "Não foi possível assumir o som", e.message);
    } finally {
      this.claiming = false;
      this.sync();
    }
  }

  command(action, value) {
    const body = { action, client_id: this.clientId };
    if (value !== undefined) body.value = value;
    api.post("/api/player/command", body)
      .then((st) => {
        if (st && typeof st.revision === "number") {
          applyEvent(this.store, "player.state", st);
          this.sync();
        }
      })
      .catch((e) => {
        if (e.code === "stage_not_connected") {
          this.hud.show("screen", "Nenhum Palco com som", "pressione Enter");
        } else this.hud.show("alert", e.message);
      });
  }

  report(event, extra = {}) {
    const t = this.track;
    if (!this.isOwner || !t) return;
    api.post("/api/player/report", {
      client_id: this.clientId,
      queue_item_id: t.itemId,
      song_id: t.songId,
      position_ms: Math.round(this.engine.position * 1000),
      playing: this.engine.playing,
      event,
      ...extra,
    }).catch(() => {});
  }

  skipFromError() {
    const t = this.track;
    if (!t || t.status !== "error" || !this.errorState) return;
    this.errorState = null;
    this.report("error", { error: t.error });
    this.hud.show("skip", "Pulando para a próxima");
  }

  setHelp(open) {
    this.help.hidden = !open;
    if (open) this.help.focus({ preventScroll: true });
    else this.root.focus({ preventScroll: true });
  }

  toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen?.().catch(() => {});
  }

  setupCursorHiding() {
    let timer = 0;
    const wake = () => {
      this.root.classList.remove("idle-cursor");
      clearTimeout(timer);
      timer = setTimeout(() => this.root.classList.add("idle-cursor"), CURSOR_IDLE_MS);
    };
    document.addEventListener("mousemove", wake);
    wake();
  }

  /** Screen Wake Lock enquanto este Palco toca (se o navegador suportar). */
  updateWakeLock() {
    const want = this.isOwner && this.engine.playing && document.visibilityState === "visible";
    if (want && !this.wakeLock && navigator.wakeLock) {
      this.wakeLock = "pending";
      navigator.wakeLock.request("screen")
        .then((lock) => {
          this.wakeLock = lock;
          lock.addEventListener("release", () => {
            if (this.wakeLock === lock) this.wakeLock = null;
          });
        })
        .catch(() => {
          this.wakeLock = null;
        });
    } else if (!want && this.wakeLock && this.wakeLock !== "pending") {
      this.wakeLock.release().catch(() => {});
      this.wakeLock = null;
    }
  }
}

async function boot() {
  const params = new URLSearchParams(location.search);
  let fetchBuffer;
  let mock = null;
  if (params.get("mock") === "1") {
    // Mock isolado (contrato §9–10) para desenvolver sem backend. Remover = apagar mock.js e este bloco.
    mock = (await import("./mock.js")).installMock(params);
    fetchBuffer = mock.fetchBuffer;
  }
  const stage = new Stage({ fetchBuffer });
  stage.start();
  if (mock) {
    globalThis.__karaokeStage = stage; // só no mock: inspeção em testes/e2e
    if (params.get("autostart") === "1") stage.unlockAndClaim();
  }
}

boot();
