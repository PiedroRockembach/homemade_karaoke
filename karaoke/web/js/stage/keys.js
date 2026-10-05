/**
 * Palco: mapa de teclado (DESIGN §7). As teclas viram `POST /api/player/command` (mesmo caminho do
 * Controle — ARCHITECTURE §4.3); o HUD aparece na hora, de forma otimista, com o valor esperado.
 * Teclas locais: F (tela cheia), ? (atalhos), Esc. Enter depende da cena (claim / play / next).
 *
 * Dono: frontend-engineer (T-007).
 */

import { fmtOffset, fmtPercent } from "../shared/format.js";

const clamp01 = (v) => Math.max(0, Math.min(1, v));
const pct = (v) => Math.round(clamp01(v) * 100);

/**
 * @typedef {object} KeyContext
 * @property {() => any} player                PlayerState atual (pode ser null)
 * @property {() => string} scene              cena visível
 * @property {() => boolean} helpOpen
 * @property {(open: boolean) => void} setHelp
 * @property {() => void} enter                ação contextual do Enter
 * @property {() => boolean} space             Espaço especial da cena (true = consumido)
 * @property {(action: string, value?: any) => void} command
 * @property {(icon: string, label: string, value?: string, pct?: number|null) => void} hud
 * @property {() => void} toggleFullscreen
 */

/** @param {KeyContext} ctx */
export function createKeyHandler(ctx) {
  let lastGuide = 0.5;
  // Valores otimistas recentes: com tecla segurada o próximo passo parte do último valor enviado,
  // não do player.state que ainda pode estar a caminho.
  const opt = {};
  const val = (p, name, fallback) => {
    const o = opt[name];
    if (o && performance.now() - o.t < 600) return o.v;
    return p[name] ?? fallback;
  };
  const remember = (name, v) => {
    opt[name] = { v, t: performance.now() };
    return v;
  };

  return function onKeyDown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const key = e.key;
    const lower = key.length === 1 ? key.toLowerCase() : key;

    if (key === "?" || (key === "/" && e.shiftKey)) {
      e.preventDefault();
      ctx.setHelp(!ctx.helpOpen());
      return;
    }
    if (key === "Escape") {
      if (ctx.helpOpen()) ctx.setHelp(false);
      else if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      return;
    }
    if (ctx.helpOpen()) return;
    if (lower === "f") {
      e.preventDefault();
      ctx.toggleFullscreen();
      return;
    }
    if (key === "Enter") {
      e.preventDefault();
      if (!e.repeat) ctx.enter();
      return;
    }

    const p = ctx.player() || {};
    switch (true) {
      case key === " " || key === "Spacebar": {
        e.preventDefault();
        if (e.repeat || ctx.space()) return;
        const willPlay = p.status !== "playing";
        ctx.command("toggle");
        ctx.hud(willPlay ? "play" : "pause", willPlay ? "Tocando" : "Pausado");
        return;
      }
      case key === "ArrowRight":
        e.preventDefault();
        ctx.command("seek_relative", 5000);
        ctx.hud("next", "Avançar", "+5 s");
        return;
      case key === "ArrowLeft":
        e.preventDefault();
        ctx.command("seek_relative", -5000);
        ctx.hud("prev", "Voltar", "−5 s");
        return;
      case key === "ArrowUp":
      case key === "ArrowDown": {
        e.preventDefault();
        const delta = key === "ArrowUp" ? 0.05 : -0.05;
        const v = remember("volume", clamp01(val(p, "volume", 0.8) + delta));
        ctx.command("volume_relative", delta);
        ctx.hud("vol", "Volume", fmtPercent(v), pct(v));
        return;
      }
      case key === "+" || key === "=" || key === "-" || key === "−" || key === "_": {
        const delta = key === "+" || key === "=" ? 0.1 : -0.1;
        const v = remember("guide_vocal", clamp01(val(p, "guide_vocal", 0) + delta));
        ctx.command("guide_vocal_relative", delta);
        ctx.hud("mic", "Voz guia", fmtPercent(v), pct(v));
        return;
      }
      case lower === "v": {
        const cur = val(p, "guide_vocal", 0);
        let v;
        if (cur > 0) {
          lastGuide = cur;
          v = 0;
        } else v = lastGuide || 0.5;
        remember("guide_vocal", v);
        ctx.command("guide_vocal", v);
        ctx.hud("mic", "Voz guia", fmtPercent(v), pct(v));
        return;
      }
      case key === "[" || key === "]": {
        const delta = key === "]" ? 50 : -50;
        const v = remember("offset_ms", Math.max(-10000, Math.min(10000, val(p, "offset_ms", 0) + delta)));
        ctx.command("offset_relative", delta);
        ctx.hud("lyrics", key === "]" ? "Letra mais tarde" : "Letra mais cedo", fmtOffset(v));
        return;
      }
      case lower === "n":
        ctx.command("next");
        ctx.hud("skip", "Próxima");
        return;
      case lower === "p":
        ctx.command("prev");
        ctx.hud("prev", "Reiniciar / anterior");
        return;
      case lower === "a": {
        const on = remember("autoplay", !val(p, "autoplay", true));
        ctx.command("autoplay", on);
        ctx.hud("refresh", on ? "Auto-avanço ligado" : "Auto-avanço desligado");
        return;
      }
    }
  };
}
