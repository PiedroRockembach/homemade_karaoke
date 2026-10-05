/**
 * Peças de "cartão de música" reutilizáveis (Controle e futura /guest):
 * miniatura 16:9 com placeholder, chips de origem da letra, linha de metadados.
 *
 * Dono: frontend-engineer (T-008).
 */

import { h, icon } from "./dom.js";
import { fmtOffset, fmtTime } from "../format.js";

/** Placeholder de miniatura (ícone de nota sobre surface-3). */
export function thumbPlaceholder() {
  return h("div", { class: "ph", title: "Miniatura indisponível" }, icon("music"));
}

/**
 * Miniatura 16:9. `src` pode ser null (placeholder). Falha de carregamento → placeholder.
 * @param {{src?: string|null, duration?: string|null, cls?: string}} opts
 */
export function thumb({ src = null, duration = null, cls = "" } = {}) {
  const box = h("div", { class: ["thumb", cls] });
  if (src) {
    const img = h("img", { src, alt: "", loading: "lazy", decoding: "async" });
    img.addEventListener("error", () => img.replaceWith(thumbPlaceholder()), { once: true });
    box.append(img);
  } else {
    box.append(thumbPlaceholder());
  }
  if (duration) box.append(h("span", { class: "dur tnum" }, duration));
  return box;
}

/**
 * Chips de origem da letra (DESIGN §11.4, rótulos do CR-001): [variante, rótulo longo, rótulo curto, ícone, title].
 * `whisper` hoje só existe quando não há letra oficial nenhuma.
 */
export const SOURCE_CHIPS = {
  aligned: [
    "chip-success",
    "Online · sincronizada pela voz",
    "Online + voz",
    "wave",
    "Letra oficial com os tempos acertados pela voz da música",
  ],
  whisper: [
    "chip-info",
    "Extraída da voz (sem letra oficial)",
    "Da voz",
    "mic",
    "Texto reconhecido da voz porque não achamos a letra oficial — pode ter palavras erradas. Cole a letra oficial.",
  ],
  lrclib: ["chip-neutral", "Online", "Online", "globe", "Letra e tempos do LRCLIB, sem ajuste pela voz"],
  online_plain: [
    "chip-neutral",
    "Online · só texto",
    "Só texto",
    "globe",
    "Letra oficial sem tempos (fonte de texto)",
  ],
  manual: ["chip-accent", "Manual", "Manual", "edit", "Letra colada ou tempos editados por você"],
};

/**
 * Chip de origem da letra; sem letra → chip neutro tracejado "Sem letra".
 * @param {string|null} source
 * @param {{label?: string, short?: boolean}} [opts] `label` do backend tem prioridade; `short` mostra os dois
 *   rótulos (longo/curto) e o CSS escolhe pela largura.
 */
export function lyricsSourceChip(source, { label = null, short = false } = {}) {
  const def = SOURCE_CHIPS[source];
  if (!def) {
    return h(
      "span",
      { class: "chip chip-neutral chip-dashed", title: "Sem letra — toca só o instrumental" },
      icon("lyrics", "icon-sm"),
      "Sem letra",
    );
  }
  const long = label || def[1];
  const text = short
    ? [h("span", { class: "lbl-long" }, long), h("span", { class: "lbl-short" }, def[2])]
    : long;
  return h("span", { class: ["chip", def[0]], title: def[4] }, icon(def[3], "icon-sm"), text);
}

/** Metadados "Artista · 3:31 · +120 ms" de uma Song. */
export function songMeta(song) {
  const parts = [];
  if (song.artist) parts.push(h("span", {}, song.artist));
  if (song.duration_ms) parts.push(h("span", { class: "tnum" }, fmtTime(song.duration_ms)));
  if (song.offset_ms) {
    parts.push(h("span", { class: "tnum", title: "Offset da letra" }, fmtOffset(song.offset_ms)));
  }
  const out = [];
  parts.forEach((p, i) => {
    if (i) out.push(h("span", { "aria-hidden": "true" }, "·"));
    out.push(p);
  });
  return out;
}

/** "1,2 MB" / "32 MB" a partir de bytes. */
export function fmtSize(bytes) {
  const mb = (Number(bytes) || 0) / (1024 * 1024);
  if (mb <= 0) return "";
  return mb < 10 ? `${mb.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} MB` : `${Math.round(mb)} MB`;
}
