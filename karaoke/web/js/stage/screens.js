/**
 * Palco: telas (cenas) e seu conteúdo — DESIGN §5.3.
 * Só manipula DOM; quem decide a cena é main.js. Atualizações por quadro (progresso, anel)
 * escrevem apenas custom properties/texto quando o valor muda.
 *
 * Dono: frontend-engineer (T-007).
 */

import { fmtTime } from "../shared/format.js";

const RING_LEN = 276.46; // 2π × 44

function setText(el, text) {
  if (el && el.textContent !== text) el.textContent = text;
}

function setPill(el, text) {
  el.hidden = !text;
  setText(el.querySelector("span"), text || "");
}

export class Screens {
  /** @param {HTMLElement} root `.stage` */
  constructor(root) {
    this.root = root;
    const q = (sel) => root.querySelector(sel);
    this.q = q;
    this.scenes = Array.from(root.querySelectorAll(".scene"));
    this.current = "";
    this.bgLayers = Array.from(root.querySelectorAll("[data-bg]"));
    this.bgIndex = 0;
    this.bgUrl = undefined;
    this.bgToken = 0;
    this.el = {
      startQueue: q("[data-start-queue]"),
      origin: q("[data-origin]"),
      title: q("[data-song-title]"),
      artist: q("[data-song-artist]"),
      singer: q("[data-song-singer]"),
      next: q("[data-next]"),
      nextTitle: q("[data-next-title]"),
      nextSinger: q("[data-next-singer]"),
      timeCur: q("[data-time-cur]"),
      timeDur: q("[data-time-dur]"),
      prog: q("[data-prog]"),
      loadingTitle: q("[data-loading-title]"),
      loadingSinger: q("[data-loading-singer]"),
      upArt: q("[data-up-art]"),
      upPh: q("[data-up-ph]"),
      upTitle: q("[data-up-title]"),
      upArtist: q("[data-up-artist]"),
      upSinger: q("[data-up-singer]"),
      ring: q("[data-ring]"),
      ringArc: q("[data-ring-arc]"),
      ringNum: q("[data-ring-num]"),
      hintsAuto: q("[data-up-hints-auto]"),
      hintsManual: q("[data-up-hints-manual]"),
      prepTitle: q("[data-prep-title]"),
      prepStage: q("[data-prep-stage]"),
      prepBar: q("[data-prep-bar]"),
      prepFill: q("[data-prep-fill]"),
      prepMeta: q("[data-prep-meta]"),
      errorMsg: q("[data-error-msg]"),
      errorAuto: q("[data-error-auto]"),
      errorWait: q("[data-error-wait]"),
      errorCount: q("[data-error-count]"),
      errorSpace: q("[data-error-space]"),
      announce: q("[data-announce]"),
    };
    this.last = {};
    setText(this.el.origin, location.origin);
  }

  /** Mostra a cena `name` (start|idle|play|loading|upnext|prep|error). */
  show(name) {
    if (name === this.current) return;
    this.current = name;
    for (const s of this.scenes) s.classList.toggle("on", s.dataset.scene === name);
    this.root.classList.toggle("in-play", name === "play");
    const label = this.scenes.find((s) => s.dataset.scene === name)?.getAttribute("aria-label") || "";
    setText(this.el.announce, label);
  }

  /** Classes de estado do palco: paused, mirror, nolyrics, offline. */
  setFlags(flags) {
    for (const [k, v] of Object.entries(flags)) this.root.classList.toggle(k, !!v);
  }

  /** Fundo = thumbnail desfocada; crossfade entre duas camadas (pré-carrega antes de trocar). */
  setBackground(url) {
    if (url === this.bgUrl) return;
    this.bgUrl = url;
    const token = ++this.bgToken;
    const swap = () => {
      if (token !== this.bgToken) return;
      const next = this.bgLayers[1 - this.bgIndex];
      const cur = this.bgLayers[this.bgIndex];
      if (url) next.style.setProperty("--bg-img", `url("${url.replace(/"/g, "%22")}")`);
      else next.style.removeProperty("--bg-img");
      next.classList.toggle("on", !!url);
      cur.classList.remove("on");
      this.bgIndex = 1 - this.bgIndex;
    };
    if (!url) return swap();
    const img = new Image();
    img.onload = swap;
    img.onerror = () => {
      if (token === this.bgToken) {
        this.bgUrl = null;
        this.bgLayers.forEach((l) => l.classList.remove("on"));
      }
    };
    img.src = url;
  }

  /** "Pressione Enter": resumo da fila. `playing` = já há música tocando (em outro Palco ou pausada). */
  renderStart(queue, playing = false) {
    const items = (queue && queue.items) || [];
    const current = queue && queue.current;
    const count = items.length + (current ? 1 : 0);
    const first = current || items[0];
    let text = "A fila está vazia — adicione músicas pelo Controle.";
    if (first) {
      const n = count === 1 ? "1 música na fila" : `${count} músicas na fila`;
      text = `${n} · ${current && playing ? "tocando agora" : "a primeira é"} “${
        first.song ? first.song.title : ""
      }”`;
    }
    setText(this.el.startQueue, text);
  }

  /** Topo do "Tocando": música, artista, cantor e "A seguir". */
  renderPlayMeta(song, singer, nextItem) {
    setText(this.el.title, song ? song.title : "");
    this.el.title.title = song ? song.title : "";
    setText(this.el.artist, song ? song.artist || "" : "");
    setPill(this.el.singer, singer || "");
    this.el.next.hidden = !nextItem;
    if (nextItem) {
      setText(this.el.nextTitle, nextItem.song ? nextItem.song.title : "");
      this.el.nextTitle.title = nextItem.song ? nextItem.song.title : "";
      setText(this.el.nextSinger, nextItem.singer || (nextItem.song && nextItem.song.artist) || "");
    }
  }

  /** Barra e tempos (posição sem offset). */
  updateProgress(posMs, durMs) {
    const sec = Math.floor(posMs / 1000);
    if (sec !== this.last.sec) {
      this.last.sec = sec;
      setText(this.el.timeCur, fmtTime(posMs));
    }
    if (durMs !== this.last.dur) {
      this.last.dur = durMs;
      setText(this.el.timeDur, fmtTime(durMs));
    }
    const p = durMs > 0 ? Math.min(1, Math.max(0, posMs / durMs)) : 0;
    const rounded = Math.round(p * 2000) / 2000;
    if (rounded !== this.last.prog) {
      this.last.prog = rounded;
      this.el.prog.style.setProperty("--prog", String(rounded));
    }
  }

  renderLoading(song, singer) {
    setText(this.el.loadingTitle, song ? `Carregando “${song.title}”…` : "Carregando…");
    setText(this.el.loadingSinger, singer ? `${singer}, prepare o microfone` : "Prepare o microfone");
  }

  /** "A seguir": `manual` = sem anel (auto-avanço desligado). */
  renderUpNext(upNext, manual) {
    const song = upNext && upNext.song;
    setText(this.el.upTitle, song ? song.title : "");
    setText(this.el.upArtist, song ? song.artist || "" : "");
    const singer = upNext && upNext.singer;
    setPill(this.el.upSinger, singer ? `${singer}, é sua vez!` : "");
    const thumb = song && song.thumbnail;
    if (thumb && this.el.upArt.getAttribute("src") !== thumb) {
      this.el.upArt.hidden = true;
      this.el.upPh.hidden = false;
      this.el.upArt.onload = () => {
        this.el.upArt.hidden = false;
        this.el.upPh.hidden = true;
      };
      this.el.upArt.onerror = () => {
        this.el.upArt.hidden = true;
        this.el.upPh.hidden = false;
      };
      this.el.upArt.src = thumb;
    } else if (!thumb) {
      this.el.upArt.hidden = true;
      this.el.upArt.removeAttribute("src");
      this.el.upPh.hidden = false;
    }
    this.el.ring.hidden = manual;
    this.el.hintsAuto.hidden = manual;
    this.el.hintsManual.hidden = !manual;
    this.last.ring = undefined;
  }

  updateRing(remainingMs, totalMs) {
    const rem = Math.max(0, remainingMs);
    const n = Math.ceil(rem / 1000);
    if (n !== this.last.ring) {
      this.last.ring = n;
      setText(this.el.ringNum, String(n));
      this.el.ring.setAttribute("aria-label", `Começa em ${n} s`);
    }
    const frac = totalMs > 0 ? Math.min(1, rem / totalMs) : 0;
    this.el.ringArc.style.setProperty("stroke-dashoffset", String(RING_LEN * (1 - frac)));
  }

  /** "Aguardando preparo": próxima ainda processando. `progress` null = indeterminado. */
  renderPrep(song, stageLabel, progress, eta) {
    setText(this.el.prepTitle, song ? `Preparando “${song.title}”` : "Preparando a próxima música");
    setText(
      this.el.prepStage,
      `${stageLabel || "Processando"}… a música começa sozinha quando ficar pronta.`,
    );
    const indet = progress == null;
    this.el.prepBar.classList.toggle("indet", indet);
    if (!indet) this.el.prepFill.style.setProperty("--prog", String(Math.max(0, Math.min(1, progress))));
    else this.el.prepFill.style.removeProperty("--prog");
    const pct = indet ? "" : Math.round(progress * 100) + "%";
    setText(this.el.prepMeta, [pct, eta].filter(Boolean).join(" · ") || "Aguardando o processamento…");
  }

  /** Erro ao tocar. `secondsLeft` null = usuário escolheu esperar aqui. */
  renderError(message, secondsLeft) {
    setText(this.el.errorMsg, message || "O áudio desta música não pôde ser carregado.");
    const waiting = secondsLeft == null;
    this.el.errorAuto.hidden = waiting;
    this.el.errorWait.hidden = !waiting;
    this.el.errorSpace.hidden = waiting;
    if (!waiting) setText(this.el.errorCount, `${Math.max(0, secondsLeft)} s`);
  }

  announce(text) {
    this.el.announce.textContent = text;
  }
}
