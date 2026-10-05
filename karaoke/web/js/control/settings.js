/**
 * Modos de letra (US-08, CR-001) e modal Configurações (DESIGN §11.2): engrenagem no header, seção "Letras" com
 * 2 radio-cards (`aligned` padrão e `online`), salva ao escolher (`PATCH /api/settings {lyrics_mode}`) com "✓ Salvo";
 * falha → mensagem + "Tentar de novo" e o radio volta ao valor do servidor. `transcribe` (alias legado) = `aligned`.
 *
 * Dono: frontend-engineer (T-003).
 */

import { api } from "../shared/api.js";
import { h, icon, replace } from "../shared/components/dom.js";
import { modalButton, openModal } from "./ui.js";

/** Modos exibidos (ordem da UI). */
export const LYRICS_MODES = {
  aligned: {
    name: "Letra oficial sincronizada pela voz",
    short: "Oficial + voz",
    desc:
      "Busca a letra oficial (LRCLIB, com ou sem tempos) e acerta o tempo de cada linha e palavra ouvindo a " +
      "voz. Sem letra oficial, o texto vem da própria voz.",
    extra: "+~20 s por música na GPU",
    recommended: true,
  },
  online: {
    name: "Letra online como está",
    short: "Online como está",
    desc: "Usa a letra e os tempos do LRCLIB sem ajuste. Mais rápido, mas às vezes fica fora do tempo. " +
      "Sem tempos online, sincroniza pela voz.",
    extra: null,
    recommended: false,
  },
};

/** Normaliza um valor vindo do servidor: `transcribe` e desconhecidos → `aligned`. */
export function normalizeMode(mode) {
  return mode === "online" ? "online" : "aligned";
}

let store = null;

export function initSettings(opts) {
  store = opts.store;
  const btn = document.getElementById("btn-settings");
  if (btn) btn.addEventListener("click", openSettings);
}

/** Modo padrão atual (settings do servidor). */
export function defaultMode() {
  const s = store && store.get().settings;
  return normalizeMode(s && s.lyrics_mode);
}

export function openSettings() {
  let serverMode = defaultMode();
  const status = h("span", { class: "saved", role: "status", "aria-live": "polite" });
  const radios = [];
  const cards = Object.entries(LYRICS_MODES).map(([value, m]) => {
    const input = h("input", {
      type: "radio",
      name: "lyrics-mode",
      value,
      checked: value === serverMode,
      "aria-describedby": `lm-d-${value}`,
      onchange: () => save(value),
    });
    radios.push(input);
    return h(
      "label",
      { class: "radio-card" },
      input,
      h(
        "div",
        {},
        h(
          "div",
          { class: "rc-t" },
          h("b", {}, m.name),
          m.recommended ? h("span", { class: "chip chip-success" }, "Recomendado") : null,
        ),
        h("p", { class: "hint", id: `lm-d-${value}` }, m.desc),
        m.extra ? h("p", { class: "hint rc-x" }, icon("clock", "icon-sm"), m.extra) : null,
      ),
    );
  });

  const setChecked = (mode) => {
    for (const r of radios) r.checked = r.value === mode;
  };

  async function save(mode) {
    replace(status);
    status.className = "saved";
    try {
      const s = await api.patch("/api/settings", { lyrics_mode: mode });
      serverMode = normalizeMode(s && s.lyrics_mode);
      store.set({ settings: s });
      setChecked(serverMode);
      replace(status, icon("check", "icon-sm"), "Salvo");
      // reinicia a animação de fade
      void status.offsetWidth;
      status.className = "saved on";
    } catch {
      setChecked(serverMode);
      status.className = "saved err";
      replace(
        status,
        "Não deu para salvar. ",
        h(
          "button",
          { class: "btn btn-ghost btn-sm", type: "button", onclick: () => save(mode) },
          "Tentar de novo",
        ),
      );
    }
  }

  openModal({
    title: "Configurações",
    body: h(
      "section",
      { class: "settings-sec", "aria-labelledby": "set-lyr-h" },
      h("h3", { class: "set-h", id: "set-lyr-h" }, "Letras"),
      h("p", { class: "hint", id: "set-lyr-d" }, "Como obter a letra das próximas músicas"),
      h(
        "div",
        {
          class: "radio-cards",
          role: "radiogroup",
          "aria-labelledby": "set-lyr-h",
          "aria-describedby": "set-lyr-d",
        },
        cards,
      ),
      h(
        "div",
        { class: "callout info" },
        icon("lyrics"),
        h(
          "div",
          {},
          "Vale para músicas novas. Para uma música pronta, use Biblioteca › Letra › ",
          h("b", {}, "Buscar letra oficial e sincronizar"),
          ".",
        ),
      ),
    ),
    footer: (close) => [status, modalButton("Fechar", { variant: "primary", onclick: () => close() })],
    initialFocus: "input[name=lyrics-mode]:checked",
  });
}
