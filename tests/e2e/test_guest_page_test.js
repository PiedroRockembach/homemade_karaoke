// Testes puros (sem DOM) da página do convidado /guest (v0.3, DESIGN §12.4, api.md §12.3).
// Rodar: .venv/bin/deno test --no-check tests/e2e/test_guest_page_test.js
// Executado também por tests/e2e/test_stage_js.py (make test). Dono: frontend-engineer (T-006, ciclo 20261005-0109).
import {
  errorText,
  filterLibrary,
  isFinalRequestError,
  limitsOf,
  myTurn,
  nameError,
  parseInviteToken,
  resultKind,
  retryAfterSeconds,
  screenForEnded,
  sessionScreenFor,
  STATUS_SCREENS,
  turnsBefore,
  turnsForNewRequest,
  turnText,
} from "../../karaoke/web/js/guest/logic.js";
import { ApiError } from "../../karaoke/web/js/shared/api.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}

const ME = { id: "g_7", name: "Bia", limits: { max_duration_ms: 900000, search_per_min: 10 } };
const item = (id, by = "host", extra = {}) => ({ id, song_id: "s" + id, added_by: by, ...extra });

// ------------------------------------------------------------------ fragmento do convite (AC-13.1)

Deno.test("lê o token do fragmento #t=", () => {
  eq(parseInviteToken("#t=Vx3kQ9-LmZp0aB7cD2eF4g"), "Vx3kQ9-LmZp0aB7cD2eF4g");
  eq(parseInviteToken("#t=Vx3kQ9-LmZp0aB7cD2eF4g&x=1"), "Vx3kQ9-LmZp0aB7cD2eF4g");
  eq(parseInviteToken("t=abcdefgh123"), "abcdefgh123");
});

Deno.test("fragmento sem token, curto ou com caracteres estranhos → null", () => {
  eq(parseInviteToken(""), null);
  eq(parseInviteToken("#"), null);
  eq(parseInviteToken("#x=abcdefgh123"), null);
  eq(parseInviteToken("#t=abc"), null);
  eq(parseInviteToken("#t=<script>alert(1)</script>"), null);
  eq(parseInviteToken(null), null);
});

Deno.test("nome: vazio, longo e válido (1–40 caracteres)", () => {
  eq(nameError("   "), "Digite seu nome para entrar.");
  eq(nameError("", "Diga quem vai cantar."), "Diga quem vai cantar.");
  eq(nameError("a".repeat(41)), "Use no máximo 40 caracteres.");
  eq(nameError("🎤".repeat(40)), "", "conta caracteres, não unidades UTF-16");
  eq(nameError("  Ana  Paula "), "");
});

// ------------------------------------------------------------------ "Sua vez em N" (AC-15.1)

Deno.test("N conta a música tocando agora", () => {
  const q = { current: item("q_1"), items: [item("q_2"), item("q_3"), item("q_4", "g_7")] };
  eq(turnsBefore(q, "q_4"), 3);
  eq(turnText(3), "Sua vez em 3 músicas");
  eq(turnsBefore(q, "q_2"), 1);
  eq(turnText(1), "Você é a próxima!");
  eq(turnsBefore(q, "q_9"), null);
});

Deno.test("sem nada tocando, o primeiro da fila é a próxima", () => {
  const q = { current: null, items: [item("q_2", "g_7")] };
  eq(turnsBefore(q, "q_2"), 0);
  eq(turnText(0), "Você é a próxima!");
  eq(turnsForNewRequest(q), 1);
  eq(turnsForNewRequest({ current: item("q_1"), items: [item("q_2"), item("q_3")] }), 3);
  eq(turnsForNewRequest(null), 0);
});

Deno.test("myTurn: meu primeiro pendente e 'é a sua vez'", () => {
  const q = { current: item("q_1", "g_7"), items: [item("q_2"), item("q_3", "g_7"), item("q_4", "g_7")] };
  const t = myTurn(q, ME);
  eq(t.mineNow, true);
  eq(t.next.item.id, "q_3");
  eq(t.next.n, 2);
  eq(myTurn({ current: null, items: [item("q_2")] }, ME), { mineNow: false, next: null });
});

// ------------------------------------------------------------------ sem cotas (DECISION 0016)

Deno.test("limites: sem cota de pedidos, só o teto de duração", () => {
  eq(limitsOf(ME), { max_duration_ms: 900000, search_per_min: 10 });
  eq(limitsOf(null), { max_duration_ms: 900000 });
  eq(limitsOf({ id: "g_1", limits: { max_duration_ms: 600000 } }).max_duration_ms, 600000);
  eq("pending_items" in limitsOf(ME) || "active_jobs" in limitsOf(ME), false);
});

// ------------------------------------------------------------------ busca (AC-14.1, AC-14.4)

Deno.test("Biblioteca: só prontas, filtro sem acento e ordem alfabética", () => {
  const songs = [
    { id: "a", title: "Garota de Ipanema", artist: "Tom Jobim", status: "ready" },
    { id: "b", title: "Evidências", artist: "Chitãozinho & Xororó", status: "ready" },
    { id: "c", title: "Evidências (ao vivo)", artist: "X", status: "processing" },
  ];
  eq(filterLibrary(songs, "").map((s) => s.id), ["b", "a"]);
  eq(filterLibrary(songs, "evidencias").map((s) => s.id), ["b"]);
  eq(filterLibrary(songs, "XOROR").map((s) => s.id), ["b"]);
});

Deno.test("tipo do resultado do YouTube", () => {
  eq(resultKind({ is_live: true, duration_ms: 0 }), "live");
  eq(resultKind({ is_long: true, duration_ms: 1000 }), "long");
  eq(resultKind({ duration_ms: 900001 }), "long");
  eq(resultKind({ duration_ms: 200000, library: { status: "ready" } }), "ready");
  eq(resultKind({ duration_ms: 200000, library: { status: "processing", job_id: "j_1" } }), "processing");
  eq(resultKind({ duration_ms: 200000, library: { status: "error" } }), "new");
  eq(resultKind({ duration_ms: 200000, library: null }), "new");
});

// ------------------------------------------------------------------ erros → mensagem / tela

Deno.test("vídeo longo vira mensagem amigável", () => {
  eq(
    errorText(new ApiError(409, "long_video_not_allowed", "x", { duration_ms: 4360000, max_ms: 900000 })),
    "Este vídeo tem 1:12:40. Na festa, só vídeos de até 15 min — procure outra versão.",
  );
});

Deno.test("429 usa retry_after_s (AC-14.5)", () => {
  const err = new ApiError(429, "rate_limited", "x", { retry_after_s: 7.2, reason: "search" });
  eq(retryAfterSeconds(err), 8);
  eq(errorText(err, "search"), "Calma! Muitas buscas seguidas. Tente em 8 s.");
  eq(errorText(err, "request"), "Calma! Tente em 8 s.");
  eq(retryAfterSeconds(new ApiError(429, "rate_limited", "x")), 10);
});

Deno.test("remoção: não é seu, tocando, rede", () => {
  eq(errorText(new ApiError(403, "not_owner", "x"), "remove"), "Esse pedido não é seu.");
  eq(
    errorText(new ApiError(409, "queue_item_playing", "x"), "remove"),
    "Essa música já está tocando — não dá para remover agora.",
  );
  eq(errorText(new ApiError(0, "network_error", "x"), "remove"), "Não deu para remover. Confira o Wi-Fi da festa.");
  eq(errorText(new ApiError(500, "internal_error", "Falhou feio.")), "Falhou feio.");
});

Deno.test("erros finais da folha viram 'Entendi'; rede e cantor inválido não", () => {
  eq(isFinalRequestError(new ApiError(409, "long_video_not_allowed", "x")), true);
  eq(isFinalRequestError(new ApiError(0, "network_error", "x")), false);
  eq(isFinalRequestError(new ApiError(400, "invalid_request", "x", { fields: { singer: "vazio" } })), false);
});

Deno.test("erros de sessão levam às telas G7/G8/G9 (AC-13.3)", () => {
  eq(sessionScreenFor(new ApiError(401, "guest_session_required", "x")), "ended");
  eq(sessionScreenFor(new ApiError(401, "guest_invite_invalid", "x")), "invite_invalid");
  eq(sessionScreenFor(new ApiError(403, "forbidden", "x", { reason: "guest_mode_off" })), "off");
  eq(sessionScreenFor(new ApiError(403, "forbidden", "x", { reason: "host_only" })), null);
  eq(sessionScreenFor(new ApiError(409, "song_not_ready", "x")), null);
  eq(STATUS_SCREENS.invite_invalid.title, "Este QR não vale mais");
  eq(screenForEnded("disabled"), "off");
  eq(screenForEnded("ended"), "ended");
  eq(screenForEnded("expired"), "ended");
});
