/**
 * Cliente da API REST (contrato docs/contracts/api.md).
 *
 * - Só URLs relativas (`/api/...`); mutações com corpo enviam `Content-Type: application/json`.
 * - Erros no formato `{error:{code,message,details}}` viram `ApiError` (a UI decide por `code`).
 * - O transporte pode ser trocado (`setTransport`) — usado apenas pelo mock `?mock=1` do Palco.
 *
 * Dono: frontend-engineer (T-007). Reutilizado pelo Controle (T-008).
 */

export class ApiError extends Error {
  /**
   * @param {number} status HTTP status (0 = sem conexão)
   * @param {string} code código do contrato (ex.: "stage_not_connected")
   * @param {string} message mensagem pt-BR pronta para exibir
   * @param {object} [details]
   */
  constructor(status, code, message, details = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const NETWORK_MESSAGE = "Sem conexão com o servidor do karaokê. Verifique se ele está rodando.";

/** Transporte padrão: fetch real. Retorna `{status, data}`; `data` = JSON ou null. */
async function fetchTransport(method, path, body) {
  const init = { method, headers: { Accept: "application/json" } };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network_error", NETWORK_MESSAGE);
  }
  let data = null;
  if (res.status !== 204) {
    const text = await res.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = null;
      }
    }
  }
  return { status: res.status, data };
}

let transport = fetchTransport;

/** Substitui o transporte (mock). `fn(method, path, body) → Promise<{status, data}>`. */
export function setTransport(fn) {
  transport = fn || fetchTransport;
}

/**
 * Faz a requisição e devolve o JSON (ou null em 204). Lança `ApiError` em status ≥ 400.
 * @param {string} method
 * @param {string} path URL relativa começando com "/"
 * @param {unknown} [body]
 */
export async function request(method, path, body) {
  const { status, data } = await transport(method, path, body);
  if (status >= 400 || status === 0) {
    const err = (data && data.error) || {};
    throw new ApiError(
      status,
      err.code || "http_" + status,
      err.message || "Algo deu errado (HTTP " + status + ").",
      err.details || {},
    );
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),
  patch: (path, body = {}) => request("PATCH", path, body),
  del: (path) => request("DELETE", path),
};

/** Monta query string ignorando valores vazios. */
export function qs(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? "?" + s : "";
}
