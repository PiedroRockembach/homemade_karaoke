// Testes puros (sem DOM) da UI do modo convidado no Controle e no Palco (v0.3, DESIGN §12.5/§12.6).
// Rodar: .venv/bin/deno test --no-check tests/e2e/test_guest_ui_test.js
// Executado também por tests/e2e/test_stage_js.py (make test). Dono: frontend-engineer (T-005, ciclo 20261005-0109).
import { qrPlacement } from "../../karaoke/web/js/stage/guest-qr.js";
import {
  enableErrorText,
  FIREWALL_HINT_AFTER_MS,
  firewallHintVisible,
  guestRequestsToastText,
  newGuestRequests,
} from "../../karaoke/web/js/control/guest-panel.js";
import { fmtAddress, fmtGuestCount, requestedByText } from "../../karaoke/web/js/shared/format.js";
import { applyEvent, createStore } from "../../karaoke/web/js/shared/state.js";
import { EVENT_TYPES } from "../../karaoke/web/js/shared/events.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}

const ON = { enabled: true, qr: "/api/guest/qr?v=3", display_url: "192.168.1.16:8765/guest" };
const OFF = { enabled: false, qr: null, display_url: null };

// ------------------------------------------------------------------ Palco: mostrar QR?

Deno.test("QR grande na Espera e no Pressione Enter com o modo ligado (AC-12.1)", () => {
  eq(qrPlacement(ON, "idle", "idle"), "lg");
  eq(qrPlacement(ON, "ended", "idle"), "lg");
  eq(qrPlacement(ON, "idle", "start"), "lg");
  eq(qrPlacement(ON, "waiting_gesture", "start"), "lg");
  eq(qrPlacement(ON, null, "idle"), "lg", "player ainda sem estado = idle");
});

Deno.test("QR menor no A seguir e no Aguardando preparo", () => {
  eq(qrPlacement(ON, "up_next", "upnext"), "sm");
  eq(qrPlacement(ON, "idle", "prep"), "sm");
});

Deno.test("QR contínuo no Palco durante a música como mini (US-17)", () => {
  eq(qrPlacement(ON, "playing", "play"), "mini");
  eq(qrPlacement(ON, "paused", "play"), "mini");
  eq(qrPlacement(ON, "playing", "start"), "mini");
  eq(qrPlacement(ON, "idle", "error"), null, "erro não mostra QR");
});

Deno.test("QR some com o modo desligado ou sem estado (AC-12.3)", () => {
  eq(qrPlacement(OFF, "idle", "idle"), null);
  eq(qrPlacement(null, "idle", "idle"), null);
  eq(qrPlacement(undefined, "up_next", "upnext"), null);
  eq(qrPlacement({ enabled: true, qr: null }, "idle", "idle"), null, "ligado sem qr");
});

// ------------------------------------------------------------------ Formatação

Deno.test("Pedido por: só quando o nome difere do cantor (AC-16.2)", () => {
  eq(requestedByText({ singer: "Bruno", added_by_name: "Carla" }), "Pedido por Carla");
  eq(requestedByText({ singer: "Carla", added_by_name: "Carla" }), "");
  eq(
    requestedByText({ singer: " carla ", added_by_name: "Carla" }),
    "",
    "sem diferenciar maiúsculas/espaços",
  );
  eq(requestedByText({ singer: null, added_by_name: "Ana" }), "Pedido por Ana");
  eq(requestedByText({ singer: "Ana", added_by_name: null }), "", "pedido do anfitrião");
  eq(requestedByText(null), "");
  eq(requestedByText({ singer: "X", added_by_name: "Bia" }, "pedido por"), "pedido por Bia");
});

Deno.test("Contagem de convidados", () => {
  eq(fmtGuestCount(0), "Nenhum convidado conectado");
  eq(fmtGuestCount(1), "1 convidado conectado");
  eq(fmtGuestCount(3), "3 convidados conectados");
  eq(fmtGuestCount(undefined), "Nenhum convidado conectado");
});

Deno.test("Rótulo do seletor de rede", () => {
  eq(
    fmtAddress({ address: "192.168.1.16", interface: "enp6s0", kind: "ethernet", recommended: true }),
    "192.168.1.16 · enp6s0 (recomendado)",
  );
  eq(
    fmtAddress({ address: "192.168.1.4", interface: "wlan0", kind: "wifi", recommended: false }),
    "192.168.1.4 · wlan0",
  );
  eq(fmtAddress({ address: "10.8.0.3", interface: "tun0", kind: "vpn" }), "10.8.0.3 · tun0 (VPN)");
  eq(fmtAddress({ address: "8.8.4.4", interface: "eth9", kind: "other" }), "8.8.4.4 · eth9");
  eq(fmtAddress(null), "");
});

// ------------------------------------------------------------------ Controle: painel

Deno.test("Dica de firewall: 60 s ligado sem ninguém, some no 1º convidado e ao dispensar", () => {
  const t0 = 1_000_000;
  const gs = { enabled: true, connected: 0, sessions: 0 };
  eq(firewallHintVisible(gs, t0, t0 + FIREWALL_HINT_AFTER_MS - 1, false), false, "antes de 60 s");
  eq(firewallHintVisible(gs, t0, t0 + FIREWALL_HINT_AFTER_MS, false), true, "60 s");
  eq(firewallHintVisible({ ...gs, sessions: 1 }, t0, t0 + 120_000, false), false, "alguém entrou");
  eq(firewallHintVisible({ ...gs, connected: 1 }, t0, t0 + 120_000, false), false, "alguém conectado");
  eq(firewallHintVisible(gs, t0, t0 + 120_000, true), false, "dispensada");
  eq(firewallHintVisible({ ...gs, enabled: false }, t0, t0 + 120_000, false), false, "desligado");
  eq(firewallHintVisible(gs, null, t0 + 120_000, false), false, "sem início conhecido");
});

Deno.test("Pedidos novos de convidados e texto do toast", () => {
  const seen = new Set(["q_1"]);
  const queue = {
    items: [
      { id: "q_1", added_by: "g_1", added_by_name: "Ana", song: { title: "Velha" } },
      { id: "q_2", added_by: "host", added_by_name: null, song: { title: "Do anfitrião" } },
      { id: "q_3", added_by: "g_2", added_by_name: "Carla", song: { title: "Evidências" } },
    ],
  };
  const fresh = newGuestRequests(seen, queue);
  eq(fresh.map((i) => i.id), ["q_3"]);
  eq(newGuestRequests(seen, queue).length, 0, "não repete");
  eq(guestRequestsToastText(fresh), "Carla pediu “Evidências”");
  eq(guestRequestsToastText([fresh[0], fresh[0], fresh[0]]), "3 novos pedidos de convidados");
  eq(guestRequestsToastText([]), "");
});

Deno.test("Mensagens de erro ao ligar", () => {
  eq(
    enableErrorText({ code: "lan_unavailable", message: "x" }),
    "nenhuma rede local encontrada. Conecte o PC ao Wi-Fi ou ao cabo da sua rede.",
  );
  eq(
    enableErrorText({
      code: "lan_bind_failed",
      message: "Não foi possível abrir a porta 8765 em 192.168.1.16.",
      details: { address: "192.168.1.16", port: 8765, reason: "EADDRINUSE" },
    }),
    "a porta 8765 já está em uso em 192.168.1.16.",
  );
  eq(
    enableErrorText({
      code: "lan_bind_failed",
      message: "Não foi possível abrir.",
      details: { reason: "EACCES" },
    }),
    "Não foi possível abrir.",
  );
});

// ------------------------------------------------------------------ Store / SSE

Deno.test("Store: guest_state do hello e evento guest.state", () => {
  eq(EVENT_TYPES.includes("guest.state") && EVENT_TYPES.includes("guest.ended"), true);
  const store = createStore();
  applyEvent(store, "hello", { client_id: "c_x", player: null, queue: null, jobs: [], guest_state: OFF });
  eq(store.get().guestState, OFF);
  applyEvent(store, "guest.state", ON);
  eq(store.get().guestState, ON);
  // hello sem guest_state (backend parcial) não apaga o que já se sabe
  applyEvent(store, "hello", { client_id: "c_x", player: null, queue: null, jobs: [] });
  eq(store.get().guestState, ON);
  // hello de convidado traz GuestMe
  applyEvent(store, "hello", { client_id: "c_y", jobs: [], guest: { id: "g_7", name: "Bia" } });
  eq(store.get().guest.id, "g_7");
});
