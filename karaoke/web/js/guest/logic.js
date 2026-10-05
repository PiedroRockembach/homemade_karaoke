/**
 * Regras puras da página /guest (sem DOM): convite no fragmento, nome, "Sua vez em N", limites do convidado,
 * tipo de cada resultado da busca e mapeamento de erro → mensagem/tela (DESIGN §12.4, api.md §12.3).
 * Testado por tests/e2e/test_guest_page_test.js.
 *
 * Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { fmtTime } from "../shared/format.js";
import { normalize } from "../shared/components/dom.js";

export const NAME_MAX = 40;
/** Sem cotas de pedidos (DECISION 0016): só o teto de duração do vídeo (api.md §12, `GuestMe.limits`). */
export const DEFAULT_LIMITS = { max_duration_ms: 900000 };
const TOKEN_RE = /^[A-Za-z0-9_-]{8,128}$/;

// ------------------------------------------------------------------ convite e nome

/**
 * Lê o token do fragmento (`#t=<token>`, AC-13.1). Devolve o token válido ou null.
 * Aceita outros parâmetros no fragmento (`#t=abc&x=1`); recusa token com caracteres fora do base64url.
 */
export function parseInviteToken(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  if (!raw) return null;
  const t = new URLSearchParams(raw).get("t");
  return t && TOKEN_RE.test(t) ? t : null;
}

/** Nome limpo: espaços nas pontas fora e espaços internos colapsados. */
export function cleanName(s) {
  return String(s || "").replace(/\s+/g, " ").trim();
}

/** Mensagem de validação do nome (cantor ou convidado); "" se válido. Conta caracteres, não bytes. */
export function nameError(s, emptyMessage = "Digite seu nome para entrar.") {
  const v = cleanName(s);
  if (!v) return emptyMessage;
  if ([...v].length > NAME_MAX) return `Use no máximo ${NAME_MAX} caracteres.`;
  return "";
}

// ------------------------------------------------------------------ fila: "Sua vez em N"

export function isMine(item, me) {
  return Boolean(item && me && me.id && item.added_by === me.id);
}

/**
 * Quantas músicas tocam antes do item (DESIGN §12.4 G4): conta a que está tocando agora.
 * Retorna null se o item não está entre os pendentes.
 */
export function turnsBefore(queue, itemId) {
  const items = (queue && queue.items) || [];
  const idx = items.findIndex((it) => it.id === itemId);
  if (idx < 0) return null;
  return idx + (queue.current ? 1 : 0);
}

/** Quantas músicas tocam antes de um pedido novo, que entra no fim. */
export function turnsForNewRequest(queue) {
  const items = (queue && queue.items) || [];
  return items.length + (queue && queue.current ? 1 : 0);
}

/** 0 ou 1 → "Você é a próxima!"; N ≥ 2 → "Sua vez em N músicas". */
export function turnText(n) {
  return n <= 1 ? "Você é a próxima!" : `Sua vez em ${n} músicas`;
}

/**
 * Resumo da vez do convidado: `{mineNow, next: {item, n} | null}`.
 * `mineNow` = o item tocando agora é meu; `next` = meu primeiro pedido pendente.
 */
export function myTurn(queue, me) {
  const mineNow = Boolean(queue && isMine(queue.current, me));
  const items = (queue && queue.items) || [];
  const item = items.find((it) => isMine(it, me));
  return { mineNow, next: item ? { item, n: turnsBefore(queue, item.id) } : null };
}

// ------------------------------------------------------------------ limites

export function limitsOf(me) {
  return { ...DEFAULT_LIMITS, ...((me && me.limits) || {}) };
}

// ------------------------------------------------------------------ busca

/** Prontas filtradas localmente (título/artista, sem acento) e em ordem alfabética do título. */
export function filterLibrary(songs, q) {
  const needle = normalize(cleanName(q));
  const list = (songs || []).filter((s) => s && s.status === "ready");
  const hit = needle ? list.filter((s) => normalize(`${s.title} ${s.artist || ""}`).includes(needle)) : list;
  return hit.slice().sort((a, b) => String(a.title).localeCompare(String(b.title), "pt-BR"));
}

/**
 * Tipo de um resultado do YouTube (DESIGN §12.4 G2):
 * "live" | "long" (sem botão) · "ready" | "processing" | "new".
 */
export function resultKind(r, maxMs = DEFAULT_LIMITS.max_duration_ms) {
  if (r.is_live) return "live";
  if (r.is_long || (Number(r.duration_ms) || 0) > maxMs) return "long";
  const st = r.library && r.library.status;
  if (st === "ready") return "ready";
  if (st === "processing") return "processing";
  return "new";
}

/** IDs de música na fila (atual + pendentes), para o chip "Já na fila". */
export function queuedSongIds(queue) {
  const ids = new Set();
  if (queue && queue.current) ids.add(queue.current.song_id);
  for (const it of (queue && queue.items) || []) ids.add(it.song_id);
  return ids;
}

// ------------------------------------------------------------------ erros

/** Segundos de espera de um 429 (details.retry_after_s), arredondado para cima; padrão 10. */
export function retryAfterSeconds(err) {
  const v = Number(err && err.details && err.details.retry_after_s);
  return Number.isFinite(v) && v > 0 ? Math.ceil(v) : 10;
}

/**
 * Erro que encerra a página e leva a uma tela de estado: "ended" (G8), "off" (G9), "invite_invalid" (G7).
 * null = erro local (mostrar mensagem e seguir).
 */
export function sessionScreenFor(err) {
  if (!err) return null;
  if (err.code === "guest_session_required") return "ended";
  if (err.code === "guest_invite_invalid") return "invite_invalid";
  if (
    err.status === 403 && err.code === "forbidden" && err.details && err.details.reason === "guest_mode_off"
  ) {
    return "off";
  }
  return null;
}

/**
 * Mensagem pt-BR para um erro de ação (`ApiError`). `action`: "request" | "remove" | "search" | "join".
 * A UI decide por `code` (api.md §0); o texto do servidor é o último recurso.
 */
export function errorText(err, action = "request") {
  const d = (err && err.details) || {};
  switch (err && err.code) {
    case "long_video_not_allowed":
      return d.duration_ms
        ? `Este vídeo tem ${fmtTime(d.duration_ms)}. Na festa, só vídeos de até ${
          Math.round((d.max_ms || DEFAULT_LIMITS.max_duration_ms) / 60000)
        } min — procure outra versão.`
        : "Na festa, só vídeos de até 15 min — procure outra versão.";
    case "rate_limited":
      return action === "search"
        ? `Calma! Muitas buscas seguidas. Tente em ${retryAfterSeconds(err)} s.`
        : `Calma! Tente em ${retryAfterSeconds(err)} s.`;
    case "not_owner":
      return "Esse pedido não é seu.";
    case "queue_item_playing":
      return "Essa música já está tocando — não dá para remover agora.";
    case "song_not_ready":
      return "Essa música não pode ser cantada agora. Tente outra versão.";
    case "song_not_found":
    case "queue_item_not_found":
      return action === "remove"
        ? "Esse pedido já saiu da fila."
        : "Essa música não existe mais. Busque de novo.";
    case "network_unavailable":
    case "youtube_error":
      return "O YouTube não respondeu. As músicas Prontas continuam disponíveis.";
    case "network_error":
      if (action === "join") return "Não deu para entrar. Confira se você está no Wi-Fi da festa.";
      if (action === "remove") return "Não deu para remover. Confira o Wi-Fi da festa.";
      return "Não deu para pedir. Confira o Wi-Fi da festa.";
    case "invalid_request":
      if (d.fields && d.fields.name) return `Digite um nome de 1 a ${NAME_MAX} caracteres.`;
      if (d.fields && d.fields.singer) return `O nome de quem canta deve ter de 1 a ${NAME_MAX} caracteres.`;
      if (action === "request") return "Transmissões ao vivo e vídeos indisponíveis não podem ser cantados.";
      return (err && err.message) || "Pedido inválido.";
    default:
      return (err && err.message) || "Algo deu errado. Tente de novo.";
  }
}

/** Erros da folha de pedido que não adianta repetir: o botão principal vira "Entendi". */
export function isFinalRequestError(err) {
  return ["long_video_not_allowed", "rate_limited", "invalid_request", "song_not_ready"]
    .includes(err && err.code) && !(err.details && err.details.fields && err.details.fields.singer);
}

// ------------------------------------------------------------------ telas de estado (G6–G10)

export const STATUS_SCREENS = {
  no_invite: {
    icon: "qr",
    tone: "",
    title: "Falta o convite",
    text: "Aponte a câmera para o QR que aparece na TV entre as músicas.",
    small: "Já entrou antes? Recarregue esta página.",
  },
  invite_invalid: {
    icon: "qr",
    tone: "warn",
    title: "Este QR não vale mais",
    text: "Escaneie o QR da TV de novo — ele muda quando o anfitrião gera um novo.",
  },
  ended: {
    icon: "logout",
    tone: "",
    title: "O anfitrião encerrou os convidados",
    text: "Seus pedidos continuam na fila. Para pedir mais, escaneie o QR da TV de novo.",
  },
  off: {
    icon: "wifi-off",
    tone: "",
    title: "Pedidos pelo celular estão desligados",
    text: "Peça ao anfitrião para ligar o Modo convidado no computador.",
    retry: "secondary",
  },
  offline: {
    icon: "wifi-off",
    tone: "danger",
    title: "Sem conexão com o karaokê",
    text:
      "Confira se você está no Wi-Fi da festa. Se o anfitrião desligou o modo convidado, peça para ligar de novo.",
    retry: "primary",
  },
};

/** Tela para o evento `guest.ended {reason}`: modo desligado → G9; encerrado/expirado → G8. */
export function screenForEnded(reason) {
  return reason === "disabled" ? "off" : "ended";
}
