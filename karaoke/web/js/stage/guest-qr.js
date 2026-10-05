/**
 * Palco: cartão do QR do modo convidado (DESIGN §12.5, US-12; ADR-0003 §7).
 *
 * Regra (AC-12.1/12.2/12.3): o QR aparece só com `guest_state.enabled` (e `qr` definido), com o player
 * entre músicas (`idle`/`ended`/`up_next`/`waiting_gesture`) e numa das cenas "Pressione Enter", "Espera"
 * (grande, `--stage-qr`), "A seguir" ou "Aguardando preparo" (menor, `--stage-qr-sm`). Nunca durante a música.
 * Aparece com fade de 700 ms; some na hora; "Novo QR" (novo `qr`) troca com crossfade de 220 ms após
 * pré-carregar a imagem (o `guest.state` chega por SSE, bem dentro dos 2 s do AC-11.3).
 *
 * Dono: frontend-engineer (T-005, ciclo 20261005-0109).
 */

const BETWEEN_SONGS = new Set(["idle", "ended", "up_next", "waiting_gesture"]);
const RETRY_MS = 5000;
const SCENE_SIZE = { start: "lg", idle: "lg", upnext: "sm", prep: "sm" };

/**
 * Onde/como mostrar o QR. Lógica pura (testada sem DOM).
 * @param {{enabled?: boolean, qr?: string|null}|null} guestState
 * @param {string|null|undefined} status `PlayerState.status` (ausente = idle)
 * @param {string} scene cena atual do Palco (start|idle|play|loading|upnext|prep|error)
 * @returns {"lg"|"sm"|null}
 */
export function qrPlacement(guestState, status, scene) {
  if (!guestState || !guestState.enabled || !guestState.qr) return null;
  if (!BETWEEN_SONGS.has(status || "idle")) return null;
  return SCENE_SIZE[scene] || null;
}

export class GuestQr {
  /** @param {HTMLElement} root `.stage` (recebe as classes `has-qr` e `qr-sm`) */
  constructor(root) {
    this.root = root;
    this.box = root.querySelector("[data-qr]");
    this.img = root.querySelector("[data-qr-img]");
    this.url = root.querySelector("[data-qr-url]");
    this.src = null; // src exibido
    this.loading = null; // src sendo pré-carregado
    this.broken = null; // src que falhou (não tenta de novo até mudar)
    this.placement = null;
    this.args = null;
  }

  /** Atualiza a partir do estado atual (chamado a cada renderização de cena). */
  update(guestState, status, scene) {
    this.args = [guestState, status, scene];
    const want = qrPlacement(guestState, status, scene);
    const src = guestState && guestState.enabled ? guestState.qr || null : null;
    if (guestState && guestState.enabled && this.url.textContent !== (guestState.display_url || "")) {
      this.url.textContent = guestState.display_url || "";
    }
    if (src && src !== this.src && src !== this.loading && src !== this.broken) this.preload(src);
    if (!src) {
      this.src = null;
      this.loading = null;
      this.img.removeAttribute("src");
    }
    // Durante a troca ("Novo QR") o anterior continua até o novo carregar (crossfade).
    const visible = Boolean(want && this.src);
    this.setVisible(visible ? want : null);
  }

  preload(src) {
    this.loading = src;
    const probe = new Image();
    probe.onload = () => {
      if (this.loading !== src) return;
      this.loading = null;
      const swapping = Boolean(this.src);
      this.src = src;
      this.img.src = src;
      if (swapping) {
        this.img.classList.remove("swap");
        void this.img.offsetWidth; // reinicia a animação de troca
        this.img.classList.add("swap");
      }
      if (this.args) this.update(...this.args);
    };
    probe.onerror = () => {
      if (this.loading !== src) return;
      this.loading = null;
      this.broken = src;
      // Falha transitória: tenta de novo em alguns segundos (se ainda for o QR atual).
      setTimeout(() => {
        if (this.broken !== src) return;
        this.broken = null;
        if (this.args) this.update(...this.args);
      }, RETRY_MS);
    };
    probe.src = src;
  }

  setVisible(placement) {
    if (placement === this.placement) return;
    this.placement = placement;
    this.root.classList.toggle("has-qr", Boolean(placement));
    this.root.classList.toggle("qr-sm", placement === "sm");
    this.box.setAttribute("aria-hidden", placement ? "false" : "true");
  }
}
