/**
 * Card "Modo convidado" do Controle (DESIGN §12.6, US-11/US-16; contrato api.md §12.2).
 *
 * Estados: carregando · indisponível (erro ao ler o estado) · desligado · ligando/desligando · ligado ·
 * erro ao ligar (`lan_unavailable`/`lan_bind_failed`) · várias redes (seletor) · dica de firewall.
 * Estado vem de `hello.guest_state` + evento `guest.state` (store `guestState`); sem ele, `GET /api/guest/state`.
 * Também: chip "Convidados N" no header e toast "Carla pediu 'X'" quando um convidado pede música.
 *
 * Dono: frontend-engineer (T-005, ciclo 20261005-0109).
 */

import { api } from "../shared/api.js";
import { h, icon, preserveFocus, replace } from "../shared/components/dom.js";
import { fmtAddress, fmtGuestCount } from "../shared/format.js";
import { modalButton, openModal, toast } from "./ui.js";

export const FIREWALL_HINT_AFTER_MS = 60_000;
const COPIED_MS = 2000;
const REQUEST_TOAST_DEBOUNCE_MS = 1000;
const REQUEST_TOAST_MAX_WAIT_MS = 5000;

// ---------------------------------------------------------------- lógica pura (testada sem DOM)

/**
 * Dica de firewall: só com o modo ligado há ≥ 60 s, ninguém entrou (0 sessões e 0 conectados)
 * e o anfitrião não dispensou.
 */
export function firewallHintVisible(gs, enabledSinceMs, nowMs, dismissed) {
  if (!gs || !gs.enabled || dismissed || enabledSinceMs == null) return false;
  if ((gs.connected || 0) > 0 || (gs.sessions || 0) > 0) return false;
  return nowMs - enabledSinceMs >= FIREWALL_HINT_AFTER_MS;
}

/** Itens novos de convidado na fila (comparando com os ids já vistos). Atualiza `seen`. */
export function newGuestRequests(seen, queue) {
  const items = (queue && queue.items) || [];
  const out = [];
  for (const it of items) {
    if (seen.has(it.id)) continue;
    seen.add(it.id);
    if (it.added_by && it.added_by !== "host" && it.added_by_name) out.push(it);
  }
  return out;
}

/** Texto do toast de pedidos: 1 → "Carla pediu “Evidências”"; vários → "3 novos pedidos de convidados". */
export function guestRequestsToastText(items) {
  if (!items.length) return "";
  if (items.length === 1) {
    const it = items[0];
    const title = (it.song && it.song.title) || it.song_id;
    return `${it.added_by_name} pediu “${title}”`;
  }
  return `${items.length} novos pedidos de convidados`;
}

/** Mensagem do callout de erro ao ligar (código do contrato → texto pt-BR). */
export function enableErrorText(err) {
  if (!err) return "";
  if (err.code === "lan_unavailable") {
    return "nenhuma rede local encontrada. Conecte o PC ao Wi-Fi ou ao cabo da sua rede.";
  }
  if (err.code === "lan_bind_failed") {
    const d = err.details || {};
    if (d.reason === "EADDRINUSE" && d.port) {
      return `a porta ${d.port} já está em uso${d.address ? ` em ${d.address}` : ""}.`;
    }
    return err.message || "a porta não pôde ser aberta.";
  }
  if (err.code === "network_error") return "sem conexão com o servidor do karaokê.";
  return err.message || "erro inesperado.";
}

// ---------------------------------------------------------------- estado do painel

let store = null;
let els = {};
const ui = {
  loading: true,
  loadError: null, // ApiError ao ler o estado
  busy: null, // "enable" | "disable" | "rotate" | "end" | "address"
  error: null, // erro do último "ligar"
  enabledSince: null, // ms (relógio local) em que vimos o modo ligar
  fwDismissed: false,
  fwTimer: 0,
  copied: false,
  copiedTimer: 0,
  copiedCmd: false,
  lastKey: "",
};
const seenQueueIds = new Set();
let queueSeeded = false;
let reqBuffer = [];
let reqTimer = 0;
let reqFirstAt = 0;

export function initGuestPanel(opts) {
  store = opts.store;
  els = {
    card: document.getElementById("guest-panel"),
    body: document.getElementById("guest-body"),
    chip: document.getElementById("guest-chip"),
  };
  if (!els.card) return;
  els.chip.addEventListener("click", () => {
    els.card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    const sw = els.card.querySelector(".switch");
    if (sw) sw.focus({ preventScroll: true });
  });
  store.subscribe((s, keys) => {
    if (keys.includes("guestState")) onGuestState(s.guestState);
    if (keys.includes("queue")) onQueue(s.queue);
  });
  render();
}

/** Chamado pelo main.js a cada `hello`: se o servidor não mandou `guest_state`, busca por REST. */
export function onHello(data) {
  if (data && data.guest_state !== undefined) return;
  loadState();
}

async function loadState() {
  ui.loading = !store.get().guestState;
  ui.loadError = null;
  render();
  try {
    const gs = await api.get("/api/guest/state");
    ui.loading = false;
    store.set({ guestState: gs });
  } catch (err) {
    ui.loading = false;
    ui.loadError = err;
    render();
  }
}

function onGuestState(gs) {
  ui.loading = false;
  ui.loadError = null;
  if (gs && gs.enabled) {
    if (ui.enabledSince == null) ui.enabledSince = Date.now();
  } else {
    ui.enabledSince = null;
    ui.fwDismissed = false;
  }
  scheduleFirewallCheck(gs);
  render();
}

function scheduleFirewallCheck(gs) {
  clearTimeout(ui.fwTimer);
  ui.fwTimer = 0;
  if (!gs || !gs.enabled || ui.fwDismissed || ui.enabledSince == null) return;
  const wait = ui.enabledSince + FIREWALL_HINT_AFTER_MS - Date.now();
  if (wait > 0) ui.fwTimer = setTimeout(() => render(), wait + 50);
}

function onQueue(queue) {
  if (!queue) return;
  if (!queueSeeded) {
    // Primeiro snapshot (hello): só registra o que já existia.
    queueSeeded = true;
    for (const it of queue.items || []) seenQueueIds.add(it.id);
    return;
  }
  const fresh = newGuestRequests(seenQueueIds, queue);
  if (!fresh.length) return;
  const now = Date.now();
  if (!reqBuffer.length) reqFirstAt = now;
  reqBuffer.push(...fresh);
  clearTimeout(reqTimer);
  const wait = Math.max(0, Math.min(REQUEST_TOAST_DEBOUNCE_MS, reqFirstAt + REQUEST_TOAST_MAX_WAIT_MS - now));
  reqTimer = setTimeout(flushRequestToast, wait);
}

function flushRequestToast() {
  const text = guestRequestsToastText(reqBuffer);
  reqBuffer = [];
  if (text) toast(text, { level: "info" });
}

// ---------------------------------------------------------------- ações

function guestCount(gs) {
  return Math.max((gs && gs.sessions) || 0, (gs && gs.connected) || 0);
}

function onSwitch() {
  const gs = store.get().guestState;
  if (ui.busy || !gs) return;
  if (!gs.enabled) {
    confirmEnable();
    return;
  }
  const n = guestCount(gs);
  if (!n) {
    disable();
    return;
  }
  openModal({
    title: `Desligar e encerrar ${n === 1 ? "1 convidado" : `${n} convidados`}?`,
    subtitle:
      "Os celulares saem da festa e o QR some do Palco. As músicas que eles pediram continuam na fila.",
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      modalButton("Desligar", {
        variant: "danger",
        onclick: () => {
          close();
          disable();
        },
      }),
    ],
  });
}

function confirmEnable() {
  openModal({
    title: "Ligar o modo convidado?",
    body: [
      h(
        "p",
        {},
        "Quem estiver nesta rede Wi-Fi e escanear o QR da TV poderá ",
        h("b", {}, "pedir músicas e ver a fila"),
        ".",
      ),
      h(
        "p",
        {},
        "Use só em ",
        h("b", {}, "rede doméstica confiável"),
        " — não em Wi-Fi público. Nada sai da sua rede.",
      ),
      h(
        "span",
        { class: "chip chip-neutral" },
        icon("clock"),
        "Desliga sozinho quando você fechar o app",
      ),
    ],
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      modalButton("Ligar", {
        variant: "primary",
        onclick: () => {
          close();
          enable();
        },
      }),
    ],
  });
}

async function run(kind, fn) {
  ui.busy = kind;
  render();
  try {
    const gs = await fn();
    ui.busy = null;
    if (gs && typeof gs === "object") store.set({ guestState: gs });
    else render();
    return true;
  } catch (err) {
    ui.busy = null;
    render();
    throw err;
  }
}

async function enable(address) {
  ui.error = null;
  const gs = store.get().guestState;
  const body = address ? { address } : {};
  try {
    await run(address && gs && gs.enabled ? "address" : "enable", () => api.post("/api/guest/enable", body));
    focusSwitch();
    if (address && gs && gs.sessions > 0) {
      toast("Rede trocada. Quem já tinha entrado precisa escanear o QR de novo.", { level: "info" });
    }
  } catch (err) {
    ui.error = err;
    render();
  }
}

async function disable() {
  ui.error = null;
  try {
    await run("disable", () => api.post("/api/guest/disable"));
    focusSwitch();
    toast("Modo convidado desligado");
  } catch (err) {
    toast(err.message || "Não foi possível desligar o modo convidado.", { level: "error" });
  }
}

async function rotate() {
  try {
    await run("rotate", () => api.post("/api/guest/rotate"));
    toast("Novo QR no Palco. Quem já entrou continua conectado.");
  } catch (err) {
    toast(err.message || "Não foi possível gerar um novo QR.", { level: "error" });
  }
}

function confirmEnd() {
  const n = guestCount(store.get().guestState);
  openModal({
    title: n ? `Encerrar ${n === 1 ? "1 convidado" : `${n} convidados`}?` : "Encerrar convidados?",
    body: h(
      "p",
      {},
      "Os celulares saem da festa e o QR atual deixa de valer. As músicas que eles pediram ",
      h("b", {}, "continuam na fila"),
      ".",
    ),
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      modalButton("Encerrar", {
        variant: "danger",
        onclick: async () => {
          close();
          try {
            await run("end", () => api.post("/api/guest/end-sessions"));
            toast("Convidados encerrados. O QR do Palco mudou.");
          } catch (err) {
            toast(err.message || "Não foi possível encerrar os convidados.", { level: "error" });
          }
        },
      }),
    ],
  });
}

async function copyText(text, input) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (input) {
      input.focus();
      input.select();
    }
    return false;
  }
}

async function copyLink() {
  const gs = store.get().guestState;
  if (!gs || !gs.url) return;
  const ok = await copyText(gs.url, els.body.querySelector("#guest-link"));
  if (!ok) {
    toast("Não deu para copiar sozinho — o link ficou selecionado: use Ctrl+C.", { level: "info" });
    return;
  }
  ui.copied = true;
  clearTimeout(ui.copiedTimer);
  ui.copiedTimer = setTimeout(() => {
    ui.copied = false;
    render();
  }, COPIED_MS);
  render();
}

async function copyCommand(cmd) {
  if (await copyText(cmd, null)) {
    ui.copiedCmd = true;
    render();
    setTimeout(() => {
      ui.copiedCmd = false;
      render();
    }, COPIED_MS);
  }
}

function focusSwitch() {
  const sw = els.card && els.card.querySelector(".switch");
  const active = document.activeElement;
  // Só recupera o foco quando ele se perdeu (o botão focado sumiu na re-renderização).
  if (sw && (!active || active === document.body)) {
    sw.focus({ preventScroll: true });
  }
}

// ---------------------------------------------------------------- render

function renderKey(gs) {
  const fw = firewallHintVisible(gs, ui.enabledSince, Date.now(), ui.fwDismissed);
  return JSON.stringify([
    gs,
    ui.loading,
    ui.loadError && ui.loadError.code,
    ui.busy,
    ui.error && ui.error.code,
    ui.error && ui.error.message,
    ui.copied,
    ui.copiedCmd,
    fw,
  ]);
}

function render() {
  if (!els.card) return;
  const gs = store.get().guestState;
  renderChip(gs);
  const key = renderKey(gs);
  if (key === ui.lastKey) return;
  ui.lastKey = key;
  preserveFocus(els.card, () => replace(els.body, ...cardContent(gs)));
}

function renderChip(gs) {
  const on = Boolean(gs && gs.enabled);
  els.chip.hidden = !on;
  if (!on) return;
  const n = (gs && gs.connected) || 0;
  const text = `Convidados ${n}`;
  const label = `Convidados: ${fmtGuestCount(n).toLowerCase()}. Ir para o Modo convidado`;
  const span = els.chip.querySelector("span");
  if (span.textContent !== text) span.textContent = text;
  els.chip.setAttribute("aria-label", label);
  els.chip.title = "Modo convidado ligado";
}

function switchButton(gs) {
  const on = Boolean(gs && gs.enabled);
  const busy = ui.busy === "enable" || ui.busy === "disable" || ui.busy === "address";
  // Ligando: o switch já mostra "ligado" com o spinner (DESIGN §12.6).
  const checked = ui.busy === "enable" ? true : ui.busy === "disable" ? false : on;
  return h("button", {
    class: "switch",
    type: "button",
    role: "switch",
    "aria-checked": String(checked),
    "aria-busy": busy ? "true" : null,
    "aria-labelledby": "h-guest",
    "aria-describedby": "guest-status",
    disabled: !gs || busy,
    dataset: { focusKey: "guest:switch" },
    onclick: onSwitch,
  });
}

function statusText(gs) {
  if (ui.busy === "enable") return "Ligando…";
  if (ui.busy === "disable") return "Desligando…";
  if (ui.busy === "address") return "Trocando de rede…";
  if (!gs) return ui.loading ? "Carregando…" : "";
  return gs.enabled ? "Ligado" : "Desligado";
}

function cardContent(gs) {
  const head = h(
    "div",
    { class: "gp-head" },
    icon("users"),
    h("h2", { id: "h-guest" }, "Modo convidado"),
    h("span", { class: "gp-status", id: "guest-status" }, statusText(gs)),
    switchButton(gs),
  );
  if (!gs) {
    if (ui.loadError) {
      return [
        head,
        h(
          "div",
          { class: "callout danger", role: "alert" },
          icon("alert"),
          h(
            "div",
            {},
            h("p", {}, "Não deu para ler o estado do modo convidado. ", ui.loadError.message || ""),
            h(
              "div",
              { class: "callout-acts" },
              h(
                "button",
                { class: "btn btn-secondary", type: "button", onclick: loadState },
                icon("refresh"),
                "Tentar de novo",
              ),
            ),
          ),
        ),
      ];
    }
    return [head, h("p", { class: "gp-desc", "aria-busy": "true" }, "Carregando…")];
  }
  if (!gs.enabled || ui.busy === "enable") return [head, ...offContent(gs)];
  return [head, ...onContent(gs)];
}

function offContent(gs) {
  const out = [h("p", { class: "gp-desc" }, "Deixe os convidados pedirem músicas pelo celular.")];
  const err = ui.error || (gs.error && ui.busy !== "enable" ? gs.error : null);
  if (err && ui.busy !== "enable") {
    out.push(
      h(
        "div",
        { class: "callout danger", role: "alert" },
        icon("alert"),
        h(
          "div",
          {},
          h("p", {}, `Não deu para abrir na rede: ${enableErrorText(err)}`),
          h(
            "div",
            { class: "callout-acts" },
            h(
              "button",
              {
                class: "btn btn-secondary",
                type: "button",
                dataset: { focusKey: "guest:retry" },
                onclick: () => enable(),
              },
              icon("refresh"),
              "Tentar de novo",
            ),
          ),
        ),
      ),
    );
  }
  return out;
}

function onContent(gs) {
  const busy = Boolean(ui.busy);
  const connected = gs.connected || 0;
  const out = [];
  out.push(
    h(
      "p",
      { class: ["gp-live", connected > 0 && "on"], role: "status" },
      h("i", { "aria-hidden": "true" }),
      h("span", {}, fmtGuestCount(connected)),
      gs.sessions > connected ? h("span", { class: "gp-muted" }, ` · ${gs.sessions} com acesso`) : null,
    ),
  );
  out.push(
    h(
      "div",
      { class: "gp-field" },
      h("label", { class: "gp-label", for: "guest-link" }, "Link do convite"),
      h(
        "div",
        { class: "gp-linkrow" },
        h("input", {
          class: "input gp-link",
          id: "guest-link",
          type: "text",
          readonly: true,
          value: gs.url || "",
          title: "O link contém o convite. Compartilhe só com quem está na festa.",
          spellcheck: "false",
          dataset: { focusKey: "guest:link" },
          onfocus: (e) => e.currentTarget.select(),
        }),
        h(
          "button",
          {
            class: "btn btn-secondary",
            type: "button",
            title: "O link contém o convite. Compartilhe só com quem está na festa.",
            disabled: !gs.url,
            dataset: { focusKey: "guest:copy" },
            onclick: copyLink,
          },
          icon(ui.copied ? "check" : "copy"),
          ui.copied ? "Copiado" : "Copiar",
        ),
      ),
    ),
  );
  const addrs = gs.addresses || [];
  if (addrs.length > 1) {
    out.push(
      h(
        "div",
        { class: "gp-field" },
        h("label", { class: "gp-label", for: "guest-ip" }, "Rede"),
        h(
          "select",
          {
            class: "input select",
            id: "guest-ip",
            disabled: busy,
            dataset: { focusKey: "guest:ip" },
            onchange: (e) => {
              const v = e.currentTarget.value;
              if (v && v !== gs.address) enable(v);
            },
          },
          addrs.map((a) =>
            h("option", { value: a.address, selected: a.address === gs.address }, fmtAddress(a))
          ),
        ),
      ),
    );
  }
  if (ui.error) {
    out.push(
      h(
        "div",
        { class: "callout danger", role: "alert" },
        icon("alert"),
        h("p", {}, `Não deu para trocar de rede: ${enableErrorText(ui.error)}`),
      ),
    );
  }
  if (firewallHintVisible(gs, ui.enabledSince, Date.now(), ui.fwDismissed)) out.push(firewallCallout(gs));
  out.push(
    h(
      "p",
      { class: "gp-note" },
      icon("info", "icon-sm"),
      "O QR aparece no Palco entre as músicas.",
    ),
  );
  out.push(
    h(
      "div",
      { class: "gp-actions" },
      h(
        "button",
        {
          class: "btn btn-secondary",
          type: "button",
          disabled: busy,
          "aria-busy": ui.busy === "rotate" ? "true" : null,
          dataset: { focusKey: "guest:rotate" },
          onclick: rotate,
        },
        icon("refresh"),
        "Novo QR",
      ),
      h(
        "button",
        {
          class: "btn btn-danger-ghost",
          type: "button",
          disabled: busy,
          "aria-busy": ui.busy === "end" ? "true" : null,
          dataset: { focusKey: "guest:end" },
          onclick: confirmEnd,
        },
        icon("logout"),
        "Encerrar convidados",
      ),
    ),
  );
  return out;
}

function firewallCallout(gs) {
  const port = gs.port || "";
  const hint = gs.firewall_hint;
  const cmds = hint && hint.command ? [h("code", {}, hint.command)] : [
    h("code", {}, `sudo ufw allow ${port}/tcp`),
    " ou ",
    h("code", {}, `sudo firewall-cmd --add-port=${port}/tcp`),
  ];
  return h(
    "div",
    { class: "callout info gp-fw" },
    icon("info"),
    h(
      "div",
      {},
      h("p", {}, h("b", {}, "Ninguém conseguiu entrar?"), " Confira:"),
      h(
        "ol",
        {},
        h("li", {}, "o celular está no ", h("b", {}, "mesmo Wi-Fi"), ";"),
        h("li", {}, "o firewall libera a porta ", h("b", { class: "tnum" }, String(port)), ": ", cmds, ";"),
        h("li", {}, "a rede escolhida", (gs.addresses || []).length > 1 ? " acima" : "", "."),
      ),
      h(
        "div",
        { class: "callout-acts" },
        hint && hint.command
          ? h(
            "button",
            {
              class: "btn btn-ghost",
              type: "button",
              dataset: { focusKey: "guest:fwcopy" },
              onclick: () => copyCommand(hint.command),
            },
            icon(ui.copiedCmd ? "check" : "copy"),
            ui.copiedCmd ? "Copiado" : "Copiar comando",
          )
          : null,
        h(
          "button",
          {
            class: "btn btn-ghost",
            type: "button",
            onclick: () => {
              ui.fwDismissed = true;
              render();
              focusSwitch();
            },
          },
          "Dispensar",
        ),
      ),
    ),
  );
}
