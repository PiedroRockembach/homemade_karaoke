/**
 * Entrada do convidado (US-13, api.md §12.3): fragmento `#t=` → `history.replaceState` → token em
 * sessionStorage até criar a sessão → tela G1 "Entrar" → `POST /api/guest/session`.
 * O nome fica em localStorage só para pré-preencher numa próxima festa (nunca o token).
 *
 * Dono: frontend-engineer (T-006, ciclo 20261005-0109).
 */

import { api } from "../shared/api.js";
import { h, icon, replace } from "../shared/components/dom.js";
import { cleanName, errorText, NAME_MAX, nameError, parseInviteToken, retryAfterSeconds } from "./logic.js";
import { callout, spinner } from "./ui.js";

const INVITE_KEY = "karaoke.guest.invite";
const NAME_KEY = "karaoke.guest.name";

function storageGet(store, key) {
  try {
    return store.getItem(key);
  } catch {
    return null;
  }
}

function storageSet(store, key, value) {
  try {
    if (value === null) store.removeItem(key);
    else store.setItem(key, value);
  } catch {
    /* armazenamento indisponível (aba privada): segue sem lembrar */
  }
}

/**
 * Tira o token da barra de endereço (AC-13.1) e o guarda na aba até a sessão existir.
 * Devolve o token do fragmento ou, se não houver, o que ficou guardado (recarregar antes de entrar).
 */
export function captureInvite() {
  const hash = location.hash;
  let token = null;
  if (hash) {
    token = parseInviteToken(hash);
    history.replaceState(null, "", "/guest");
    if (token) storageSet(sessionStorage, INVITE_KEY, token);
  }
  return token || parseInviteToken("#t=" + (storageGet(sessionStorage, INVITE_KEY) || ""));
}

export function forgetInvite() {
  storageSet(sessionStorage, INVITE_KEY, null);
}

export function recallName() {
  return storageGet(localStorage, NAME_KEY) || "";
}

/** "Já estou dentro?" → GuestMe ou lança ApiError (401 sem sessão). */
export async function currentGuest() {
  const res = await api.get("/api/guest/session");
  return res.guest;
}

/**
 * Tela G1 "Entrar". `onJoined(guest)` ao criar a sessão; `onScreen(kind)` para convite inválido / modo desligado.
 */
export function renderJoin(container, { token, onJoined, onScreen }) {
  const input = h("input", {
    id: "join-name",
    name: "name",
    class: "input",
    maxlength: String(NAME_MAX),
    autocomplete: "nickname",
    enterkeyhint: "go",
    autocapitalize: "words",
    spellcheck: "false",
    value: recallName(),
    "aria-describedby": "join-hint",
  });
  const hintText = h("span", {}, "Aparece na TV quando for sua vez.");
  const counter = h("span", { class: "tnum", "aria-hidden": "true" });
  const hint = h("div", { class: "hint", id: "join-hint" }, hintText, counter);
  const btnLabel = h("span", {}, "Entrar na festa");
  const btnIcon = icon("arrow-right");
  const btn = h("button", { type: "submit", class: "btn btn-primary btn-lg" }, btnLabel, btnIcon);
  const errBox = h("div", { class: "join-err", "aria-live": "polite" });
  let busy = false;

  const updateCounter = () => {
    const n = [...input.value].length;
    counter.textContent = n >= 30 ? `${n}/${NAME_MAX}` : "";
  };
  const setInvalid = (msg) => {
    input.setAttribute("aria-invalid", msg ? "true" : "false");
    hint.classList.toggle("err", Boolean(msg));
    hintText.textContent = msg || "Aparece na TV quando for sua vez.";
  };
  const setBusy = (on) => {
    busy = on;
    input.readOnly = on;
    btn.setAttribute("aria-busy", on ? "true" : "false");
    replace(btn, on ? [spinner(), "Entrando…"] : [btnLabel, btnIcon]);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    replace(errBox);
    const msg = nameError(input.value);
    if (msg) {
      setInvalid(msg);
      input.focus();
      return;
    }
    setInvalid("");
    const name = cleanName(input.value);
    setBusy(true);
    try {
      const res = await api.post("/api/guest/session", { token, name });
      storageSet(localStorage, NAME_KEY, name);
      forgetInvite();
      onJoined(res.guest);
    } catch (err) {
      setBusy(false);
      if (err.code === "guest_invite_invalid") {
        forgetInvite();
        onScreen("invite_invalid");
      } else if (err.status === 403) {
        onScreen("off");
      } else if (
        err.code === "invalid_request" && err.details && err.details.fields && err.details.fields.name
      ) {
        setInvalid(errorText(err, "join"));
        input.focus();
      } else if (err.code === "rate_limited") {
        replace(errBox, callout("warning", "clock", `Calma! Tente em ${retryAfterSeconds(err)} s.`));
      } else {
        replace(
          errBox,
          callout("danger", "wifi-off", errorText(err, "join"), [
            h(
              "button",
              { type: "button", class: "btn btn-secondary", onclick: submit },
              icon("refresh"),
              "Tentar de novo",
            ),
          ]),
        );
      }
    }
  };

  input.addEventListener("input", () => {
    updateCounter();
    if (input.getAttribute("aria-invalid") === "true") setInvalid("");
  });
  updateCounter();

  replace(
    container,
    h(
      "div",
      { class: "join" },
      h("span", { class: "logo-dot", "aria-hidden": "true" }),
      h("h1", {}, "Bem-vindo à festa!"),
      h("p", { class: "lead" }, "Escolha músicas e entre na fila direto do celular."),
      h(
        "form",
        { novalidate: true, onsubmit: submit },
        h("div", { class: "field" }, h("label", { for: "join-name" }, "Seu nome"), input),
        hint,
        errBox,
        btn,
      ),
      h("p", { class: "priv" }, icon("lock", "icon-sm"), "Rede local · nada é enviado para a internet."),
    ),
  );
}
