/**
 * Conexão SSE em `/api/events?client_id=&role=` (contrato §10).
 *
 * - `client_id` estável por aba (sessionStorage), formato `c_<aleatório>`.
 * - Despacha cada tipo de evento para `onEvent(type, data)`; a cada (re)conexão chega um `hello`.
 * - O EventSource reconecta sozinho; se ele desistir (readyState CLOSED, ex.: resposta não-200),
 *   reconectamos com backoff.
 * - A fábrica do EventSource pode ser trocada (`setEventSourceFactory`) — usado só pelo mock `?mock=1`.
 *
 * Dono: frontend-engineer (T-007). Reutilizado pelo Controle (T-008).
 */

export const EVENT_TYPES = [
  "hello",
  "player.state",
  "player.position",
  "queue.updated",
  "job.updated",
  "job.removed",
  "library.updated",
  "song.updated",
  "lyrics.updated",
  "lyrics.offset_detected",
  "settings.updated",
  "system.updated",
  "toast",
];

const STORAGE_KEY = "karaoke.client_id";

function randomId() {
  const bytes = new Uint8Array(9);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("");
}

/** client_id da aba (persistido em sessionStorage; cai para memória se indisponível). */
export function getClientId() {
  let id = null;
  try {
    id = sessionStorage.getItem(STORAGE_KEY);
  } catch {
    id = null;
  }
  if (!id || !/^c_[A-Za-z0-9_-]{1,64}$/.test(id)) {
    id = "c_" + randomId();
    try {
      sessionStorage.setItem(STORAGE_KEY, id);
    } catch {
      /* sessionStorage indisponível: id só desta carga */
    }
  }
  return id;
}

let factory = (url) => new EventSource(url);

/** Substitui a criação do EventSource (mock). `fn(url) → objeto compatível com EventSource`. */
export function setEventSourceFactory(fn) {
  factory = fn || ((url) => new EventSource(url));
}

/**
 * Abre a conexão SSE.
 * @param {{role: "control"|"stage", clientId: string,
 *          onEvent: (type: string, data: any) => void,
 *          onStatus?: (status: "connecting"|"open"|"reconnecting") => void}} opts
 * @returns {{close: () => void}}
 */
export function connectEvents({ role, clientId, onEvent, onStatus = () => {} }) {
  const url = "/api/events?client_id=" + encodeURIComponent(clientId) + "&role=" + encodeURIComponent(role);
  let es = null;
  let closed = false;
  let retryMs = 1000;
  let retryTimer = 0;

  const open = () => {
    if (closed) return;
    onStatus("connecting");
    es = factory(url);
    es.addEventListener("open", () => {
      retryMs = 1000;
      onStatus("open");
    });
    es.addEventListener("error", () => {
      if (closed) return;
      onStatus("reconnecting");
      if (es.readyState === 2 /* CLOSED */) {
        es.close();
        clearTimeout(retryTimer);
        retryTimer = setTimeout(open, retryMs);
        retryMs = Math.min(retryMs * 2, 10000);
      }
    });
    for (const type of EVENT_TYPES) {
      es.addEventListener(type, (ev) => {
        let data = null;
        try {
          data = ev.data ? JSON.parse(ev.data) : null;
        } catch {
          return;
        }
        onEvent(type, data);
      });
    }
  };

  open();
  return {
    close() {
      closed = true;
      clearTimeout(retryTimer);
      if (es) es.close();
    },
  };
}
