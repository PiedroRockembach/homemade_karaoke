/**
 * Aplica o tema salvo antes da primeira pintura (script clássico e síncrono no <head>; evita piscar).
 * O main.js depois sincroniza com `settings.theme` do servidor.
 *
 * Dono: frontend-engineer (T-008).
 */
(function () {
  try {
    const t = localStorage.getItem("karaoke.theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch {
    /* sem localStorage: segue prefers-color-scheme */
  }
})();
