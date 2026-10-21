/**
 * Modos de letra (US-08, CR-001) e modal Configurações (DESIGN §11.2): engrenagem no header, seção "Letras" com
 * 2 radio-cards (`aligned` padrão e `online`), salva ao escolher (`PATCH /api/settings {lyrics_mode}`) com "✓ Salvo";
 * falha → mensagem + "Tentar de novo" e o radio volta ao valor do servidor. `transcribe` (alias legado) = `aligned`.
 * ADR-0005 (US-24): seção "Fontes de letra" — interruptor `extra_lyrics_sources` e campo de senha da chave da API do
 * Vagalume ("Salvar"/"Remover", estado por `vagalume_api_key_set`; a chave nunca é exibida nem devolvida).
 * ADR-0004 (US-20): seção "Palco" — interruptor "Pontuação e guia de notas" (`scoring_enabled`).
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

/** Nomes das fontes de letra (`provider` do contrato → texto da UI). */
export const PROVIDER_NAMES = { lrclib: "LRCLIB", vagalume: "Vagalume", lyricsovh: "lyrics.ovh" };

/** Fontes extras ligadas? (`extra_lyrics_sources`, padrão `true` quando ainda não carregado). */
export function extraSourcesOn() {
  const s = store && store.get().settings;
  return !(s && s.extra_lyrics_sources === false);
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

  /** PATCH genérico: sucesso → "✓ Salvo" e UI pelo servidor; falha → UI volta ao servidor + "Tentar de novo". */
  async function patch(body, onOk = null) {
    replace(status);
    status.className = "saved";
    try {
      const s = await api.patch("/api/settings", body);
      store.set({ settings: s });
      sync(s);
      if (onOk) onOk();
      replace(status, icon("check", "icon-sm"), "Salvo");
      // reinicia a animação de fade
      void status.offsetWidth;
      status.className = "saved on";
    } catch {
      sync(store.get().settings);
      status.className = "saved err";
      replace(
        status,
        "Não deu para salvar. ",
        h(
          "button",
          { class: "btn btn-ghost btn-sm", type: "button", onclick: () => patch(body, onOk) },
          "Tentar de novo",
        ),
      );
    }
  }

  const save = (mode) => patch({ lyrics_mode: mode });

  // --- Fontes de letra (ADR-0005) ---
  const extraSwitch = h("button", {
    class: "switch",
    type: "button",
    role: "switch",
    id: "set-extra",
    "aria-checked": "true",
    "aria-labelledby": "set-extra-l",
    "aria-describedby": "set-extra-d",
    onclick: () => {
      const next = extraSwitch.getAttribute("aria-checked") !== "true";
      extraSwitch.setAttribute("aria-checked", String(next));
      patch({ extra_lyrics_sources: next });
    },
  });
  // --- Pontuação e guia de notas (ADR-0004, US-20) ---
  const scoreSwitch = h("button", {
    class: "switch",
    type: "button",
    role: "switch",
    id: "set-score",
    "aria-checked": "true",
    "aria-labelledby": "set-score-l",
    "aria-describedby": "set-score-d",
    onclick: () => {
      const next = scoreSwitch.getAttribute("aria-checked") !== "true";
      scoreSwitch.setAttribute("aria-checked", String(next));
      patch({ scoring_enabled: next });
    },
  });
  const keyInput = h("input", {
    class: "input",
    id: "set-vkey",
    type: "password",
    autocomplete: "off",
    spellcheck: "false",
    maxlength: "200",
    "aria-describedby": "set-vkey-d set-vkey-s",
    oninput: () => updateKeyButtons(),
    onkeydown: (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        saveKey();
      }
    },
  });
  const keyState = h("span", {
    class: "hint set-key-state",
    id: "set-vkey-s",
    role: "status",
    "aria-live": "polite",
  });
  const keySave = h(
    "button",
    { class: "btn btn-secondary", type: "button", onclick: () => saveKey() },
    "Salvar",
  );
  const keyRemove = h(
    "button",
    {
      class: "btn btn-ghost",
      type: "button",
      onclick: () => patch({ vagalume_api_key: null }, () => keyInput.focus()),
    },
    "Remover",
  );
  let keySet = false;

  function updateKeyButtons() {
    keySave.disabled = !keyInput.value.trim();
    keyRemove.hidden = !keySet;
  }

  function saveKey() {
    const v = keyInput.value.trim();
    if (!v) return;
    patch({ vagalume_api_key: v }, () => {
      keyInput.value = "";
      // "Salvar" fica desabilitado com o campo vazio: o foco vai para o campo (estável).
      keyInput.focus();
      updateKeyButtons();
    });
  }

  /** Reflete as settings do servidor nos controles do modal. */
  function sync(s) {
    serverMode = normalizeMode(s && s.lyrics_mode);
    setChecked(serverMode);
    extraSwitch.setAttribute("aria-checked", String(!(s && s.extra_lyrics_sources === false)));
    scoreSwitch.setAttribute("aria-checked", String(!(s && s.scoring_enabled === false)));
    keySet = Boolean(s && s.vagalume_api_key_set);
    keyState.textContent = keySet ? "Chave configurada (usa a API oficial)." : "Nenhuma chave configurada.";
    updateKeyButtons();
  }
  sync(store.get().settings);

  const sourcesSec = h(
    "section",
    { class: "settings-sec", "aria-labelledby": "set-src-h" },
    h("h3", { class: "set-h", id: "set-src-h" }, "Fontes de letra"),
    h(
      "div",
      { class: "set-row" },
      h(
        "div",
        {},
        h("b", { id: "set-extra-l" }, "Buscar letra em outras fontes (Vagalume, lyrics.ovh)"),
        h(
          "p",
          { class: "hint", id: "set-extra-d" },
          "Quando o LRCLIB não tem a letra, procura também nesses sites. O LRCLIB é sempre consultado.",
        ),
      ),
      extraSwitch,
    ),
    h(
      "div",
      { class: "field" },
      h(
        "label",
        { for: "set-vkey" },
        "Chave da API do Vagalume ",
        h("span", { class: "optional" }, "(opcional)"),
      ),
      h("div", { class: "set-key" }, keyInput, keySave, keyRemove),
      h(
        "p",
        { class: "hint", id: "set-vkey-d" },
        "Sem chave, a app lê a página pública do Vagalume. A chave fica só neste computador e nunca é exibida.",
      ),
      keyState,
    ),
  );

  openModal({
    title: "Configurações",
    body: [
      h(
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
      sourcesSec,
      h(
        "section",
        { class: "settings-sec", "aria-labelledby": "set-stage-h" },
        h("h3", { class: "set-h", id: "set-stage-h" }, "Palco"),
        h(
          "div",
          { class: "set-row" },
          h(
            "div",
            {},
            h("b", { id: "set-score-l" }, "Pontuação e guia de notas"),
            h(
              "p",
              { class: "hint", id: "set-score-d" },
              "Avalia a afinação do cantor pelo microfone e exibe as barras de notas no Palco. Atalho no Palco: S.",
            ),
          ),
          scoreSwitch,
        ),
      ),
    ],
    footer: (close) => [status, modalButton("Fechar", { variant: "primary", onclick: () => close() })],
    initialFocus: "input[name=lyrics-mode]:checked",
  });
}
