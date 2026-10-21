// Testes puros (sem DOM) do card do modo convidado — diagnósticos e dica de firewall (ADR-0006 §6).
// Rodar: .venv/bin/deno test --no-check tests/e2e/test_guest_diagnostics_test.js
// Dono: frontend-engineer (T-004, ciclo 20261021-0239-karaoke).
import {
  firewallHintCommands,
  isWslPlatform,
  visibleDiagnostics,
  WINDOWS_NETWORK_COMMAND,
} from "../../karaoke/web/js/control/guest-panel.js";

function eq(actual, expected, msg = "") {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`${msg} esperado ${JSON.stringify(expected)}, obtido ${JSON.stringify(actual)}`);
  }
}

Deno.test("isWslPlatform reconhece as plataformas WSL", () => {
  eq(isWslPlatform({ platform: "wsl-nat" }), true);
  eq(isWslPlatform({ platform: "wsl-mirrored" }), true);
  eq(isWslPlatform({ platform: "wsl-unknown" }), true);
  eq(isWslPlatform({ platform: "linux" }), false);
  eq(isWslPlatform({}), false, "v0.3 sem platform");
  eq(isWslPlatform(null), false);
});

Deno.test("firewallHintCommands: windows, linux com dica e fallback", () => {
  eq(
    firewallHintCommands({
      port: 8765,
      firewall_hint: { tool: "windows", command: "karaoke.bat --configurar-rede" },
    }),
    { windows: true, commands: ["karaoke.bat --configurar-rede"] },
    "dica windows",
  );
  eq(
    firewallHintCommands({ port: 8765, platform: "wsl-nat", firewall_hint: null }),
    { windows: true, commands: [WINDOWS_NETWORK_COMMAND] },
    "WSL sem dica",
  );
  eq(
    firewallHintCommands({ port: 8765, firewall_hint: { tool: "ufw", command: "sudo ufw allow 8765/tcp" } }),
    { windows: false, commands: ["sudo ufw allow 8765/tcp"] },
    "ufw",
  );
  eq(
    firewallHintCommands({ port: 8766, platform: "linux", firewall_hint: null }),
    { windows: false, commands: ["sudo ufw allow 8766/tcp", "sudo firewall-cmd --add-port=8766/tcp"] },
    "linux sem dica",
  );
});

Deno.test("visibleDiagnostics: filtra, deduplica e põe avisos primeiro", () => {
  const gs = {
    diagnostics: [
      { code: "vpn_present", severity: "info", message: "Uma VPN está ativa.", command: null },
      {
        code: "wsl_nat_unconfigured",
        severity: "warning",
        message: "Sem encaminhamento.",
        command: "karaoke.bat --configurar-rede",
      },
      { code: "wsl_nat_unconfigured", severity: "warning", message: "dup", command: null },
      { code: "", severity: "warning", message: "sem code" },
      { code: "x", severity: "warning", message: "" },
      null,
      { code: "windows_network_public", severity: "warning", message: "Rede pública.", command: "" },
      { code: "novo", severity: "estranho", message: "Desconhecido." },
    ],
  };
  eq(visibleDiagnostics(gs).map((d) => [d.code, d.severity, d.command]), [
    ["wsl_nat_unconfigured", "warning", "karaoke.bat --configurar-rede"],
    ["windows_network_public", "warning", null],
    ["vpn_present", "info", null],
    ["novo", "info", null],
  ]);
  eq(visibleDiagnostics({}), [], "v0.3 sem diagnostics");
  eq(visibleDiagnostics({ diagnostics: "x" }), [], "tipo inválido");
  eq(visibleDiagnostics(null), []);
});
