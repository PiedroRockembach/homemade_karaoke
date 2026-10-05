/**
 * Guarda de inicialização da página /guest (FIX-003). Script CLÁSSICO, carregado antes do módulo `main.js`,
 * escrito em ES5 de propósito: precisa rodar até em navegadores que não entendem o resto da página.
 *
 * - Sem suporte a módulos (`noModule`) → tela "navegador muito antigo".
 * - Erro de script, falha ao baixar um módulo ou promessa rejeitada ANTES do sinal de boot → tela
 *   "Não consegui abrir a página neste navegador" com a mensagem do erro visível (para fotografar).
 * - Sem sinal de boot em BOOT_TIMEOUT_MS → a mesma tela, com "módulos não carregaram".
 * - O módulo avisa que subiu com `window.karaokeGuestGuard.ok()` e pode relatar falha com `.fail(err)`
 *   (`.fail` mostra a tela mesmo depois do `ok()`: é usado para erro de JS durante o boot).
 *   Depois do `ok()`, erros não trocam mais a tela (a página já funciona; o módulo trata os próprios erros).
 *
 * Dono: frontend-engineer (FIX-003, ciclo 20261005-0109).
 */
// ES5 de propósito (roda antes de saber se o navegador entende o resto): `var` e `window` são intencionais.
// deno-lint-ignore-file no-var no-window no-window-prefix no-inner-declarations
(function () {
  "use strict";

  var BOOT_TIMEOUT_MS = 6000;
  var booted = false;
  var failed = false;
  var errors = [];

  function describe(err) {
    if (!err) return "erro desconhecido";
    if (typeof err === "string") return err;
    var msg = (err.name ? err.name + ": " : "") + (err.message || String(err));
    return msg;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.appendChild(document.createTextNode(text));
    return node;
  }

  function render(title, text) {
    var status = document.getElementById("status");
    if (!status) return false;
    var ids = ["boot", "join", "app"];
    for (var i = 0; i < ids.length; i++) {
      var other = document.getElementById(ids[i]);
      if (other) other.hidden = true;
    }
    while (status.firstChild) status.removeChild(status.firstChild);

    var box = el("div", "empty");
    var h1 = el("h1", "", title);
    h1.id = "state-title";
    h1.setAttribute("tabindex", "-1");
    box.appendChild(h1);
    box.appendChild(el("p", "", text));
    if (errors.length) {
      box.appendChild(el("p", "small", "Detalhes do erro (tire uma foto e mostre ao anfitrião):"));
      box.appendChild(el("pre", "boot-err", errors.join("\n") + "\n\n" + navigator.userAgent));
    }
    var btn = el("button", "btn btn-primary btn-lg", "Tentar de novo");
    btn.type = "button";
    btn.addEventListener("click", function () {
      location.reload();
    });
    box.appendChild(btn);

    var screen = el("div", "state");
    screen.appendChild(box);
    status.appendChild(screen);
    status.hidden = false;
    return true;
  }

  function fail(title, text, force) {
    if (failed || (booted && !force)) return;
    failed = true;
    var draw = function () {
      render(title, text);
    };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw);
    else draw();
  }

  function failBrowser(detail, force) {
    if (detail) errors.push(detail);
    fail(
      "Não consegui abrir a página neste navegador",
      "Tente abrir o link no Chrome ou no Safari atualizados. Se continuar, mostre esta tela ao anfitrião.",
      force,
    );
  }

  if (!("noModule" in document.createElement("script"))) {
    errors.push("Sem suporte a módulos JavaScript (nomodule).");
    fail(
      "Navegador muito antigo",
      "Este navegador é antigo demais para a página da festa. Abra o link no Chrome ou no Safari atualizados.",
    );
    return;
  }

  // Captura: pega erros de script e também falhas ao baixar <script>/<link> (que não sobem até o window).
  window.addEventListener("error", function (ev) {
    if (booted) return;
    var t = ev.target;
    if (t && t !== window && (t.tagName === "SCRIPT" || t.tagName === "LINK")) {
      failBrowser("Falha ao carregar " + (t.src || t.href || t.tagName));
      return;
    }
    var where = ev.filename ? " (" + ev.filename + ":" + ev.lineno + ":" + ev.colno + ")" : "";
    failBrowser(describe(ev.error || ev.message) + where);
  }, true);

  window.addEventListener("unhandledrejection", function (ev) {
    if (booted) return;
    failBrowser("Promessa rejeitada: " + describe(ev.reason));
  });

  var timer = setTimeout(function () {
    if (booted) return;
    errors.push("Os módulos da página não carregaram em " + BOOT_TIMEOUT_MS / 1000 + " s.");
    failBrowser("");
  }, BOOT_TIMEOUT_MS);

  window.karaokeGuestGuard = {
    ok: function () {
      booted = true;
      clearTimeout(timer);
    },
    fail: function (err) {
      clearTimeout(timer);
      failBrowser(describe(err) + (err && err.stack ? "\n" + err.stack : ""), true);
    },
  };
})();
