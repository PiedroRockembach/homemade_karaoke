/**
 * Fluxos de ação compartilhados entre Busca e Biblioteca:
 * "Preparar", "Preparar e enfileirar" (modal "Quem vai cantar?"), confirmação de vídeo longo,
 * "Adicionar à fila" e comandos do player. v0.2 (T-003): override do modo de letra por música (`lyrics_mode`).
 *
 * Dono: frontend-engineer (T-008).
 */

import { api } from "../shared/api.js";
import { h, icon } from "../shared/components/dom.js";
import { defaultMode, LYRICS_MODES } from "./settings.js";
import { modalButton, openModal, toast, toastError, withBusy } from "./ui.js";

/** @type {{store: any, clientId: string, onStageNotConnected: () => void}} */
const ctx = { store: null, clientId: "", onStageNotConnected: () => {} };

export function initActions(opts) {
  Object.assign(ctx, opts);
}

/** Nome curto para toasts: título limpo da música ou do resultado. */
function shortTitle(x) {
  return (x && (x.title || x.original_title)) || "Música";
}

/** Cantores recentes (fila atual + histórico), para o datalist do modal. */
function recentSingers() {
  const q = ctx.store.get().queue;
  const names = new Set();
  if (q) {
    for (const it of [q.current, ...(q.items || []), ...(q.history || [])]) {
      if (it && it.singer) names.add(it.singer);
    }
  }
  return [...names].slice(0, 12);
}

/** "1h12min" / "18 min" */
function fmtLong(ms) {
  const min = Math.round((Number(ms) || 0) / 60000);
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)}h${String(min % 60).padStart(2, "0")}min`;
}

/**
 * Modal "Quem vai cantar?". Resolve com o nome (string, possivelmente vazia) ou null se cancelado.
 * `submit(singer)` roda com o botão em carregamento; se lançar, o modal continua aberto.
 */
function askSinger({ title, subtitle, confirmLabel, iconId, submit, mode = null }) {
  const input = h("input", {
    class: "input",
    id: "singer",
    name: "singer",
    maxlength: "40",
    placeholder: "Ex.: Ana",
    autocomplete: "off",
    list: "singers",
  });
  const singers = recentSingers();
  const form = h(
    "form",
    {
      class: "field",
      id: "singer-form",
      onsubmit: async (e) => {
        e.preventDefault();
        const btn = modal.el.querySelector(".modal-foot .btn-primary");
        try {
          await withBusy(btn, () => submit(input.value.trim()));
          modal.close();
        } catch (err) {
          toastError(err);
        }
      },
    },
    h("label", { for: "singer" }, "Quem vai cantar? ", h("span", { class: "optional" }, "(opcional)")),
    input,
    singers.length ? h("datalist", { id: "singers" }, singers.map((s) => h("option", { value: s }))) : null,
    h("span", { class: "hint" }, "O nome aparece no Palco na tela “A seguir”."),
    mode ? modeLine(mode) : null,
  );
  const modal = openModal({
    title,
    subtitle,
    body: form,
    footer: (close) => [
      modalButton("Cancelar", { onclick: () => close() }),
      h(
        "button",
        { class: "btn btn-primary", type: "submit", form: "singer-form" },
        icon(iconId),
        confirmLabel,
      ),
    ],
  });
}

/**
 * Linha "Letra: <modo> (padrão) [Alterar]" do modal (DESIGN §11.3). `mode.value` = override ou null (padrão).
 * Alterar troca por um `<select>` com os modos.
 */
function modeLine(mode) {
  const def = defaultMode();
  const name = (m) => LYRICS_MODES[m || def].name;
  const label = h("span", { id: "mode-cur" });
  const paint = () => {
    label.replaceChildren(
      "Letra: ",
      h("b", {}, name(mode.value)),
      !mode.value || mode.value === def ? " (padrão)" : "",
    );
  };
  paint();
  const select = h(
    "select",
    { class: "input select", id: "mode-sel", "aria-label": "Como obter a letra desta música" },
    Object.entries(LYRICS_MODES).map(([k, m]) =>
      h("option", { value: k, selected: k === (mode.value || def) }, m.name + (k === def ? " (padrão)" : ""))
    ),
  );
  select.addEventListener("change", () => {
    mode.value = select.value === def ? null : select.value;
    paint();
  });
  const line = h("div", { class: "mode-line" }, icon("lyrics", "icon-sm"), label);
  const change = h("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    "aria-describedby": "mode-cur",
    onclick: () => {
      line.classList.add("open");
      select.focus();
    },
  }, "Alterar");
  line.append(change, select);
  return line;
}

/** Modal de vídeo longo. Resolve true ("Preparar mesmo assim") ou false. */
function confirmLong(r, durationMs) {
  return new Promise((resolve) => {
    let decided = false;
    openModal({
      title: `Este vídeo é longo (${fmtLong(durationMs)})`,
      subtitle: r.title,
      body: h(
        "div",
        { class: "callout warning" },
        icon("alert"),
        h(
          "div",
          {},
          "Vídeos com mais de 15 minutos costumam ser coletâneas ou shows. O preparo vai demorar bem mais e " +
            "ocupar mais espaço em disco, e a letra provavelmente não será encontrada.",
        ),
      ),
      footer: (close) => [
        modalButton("Escolher outro", { onclick: () => close() }),
        modalButton("Preparar mesmo assim", {
          variant: "primary",
          onclick: () => {
            decided = true;
            close(false);
            resolve(true);
          },
        }),
      ],
      onClose: () => {
        if (!decided) resolve(false);
      },
    });
  });
}

/** POST /api/jobs tratando `long_video_confirmation_required`. Retorna a resposta ou null (desistiu). */
async function postJob(r, { enqueue, singer, confirm_long, lyricsMode = null }) {
  const body = {
    youtube_id: r.youtube_id,
    title: r.title,
    channel: r.channel,
    duration_ms: r.duration_ms,
    enqueue,
    singer: singer || null,
    confirm_long: Boolean(confirm_long),
  };
  if (lyricsMode) body.lyrics_mode = lyricsMode;
  try {
    return await api.post("/api/jobs", body);
  } catch (err) {
    if (err.code === "long_video_confirmation_required" && !confirm_long) {
      const ok = await confirmLong(r, err.details.duration_ms || r.duration_ms);
      if (!ok) return null;
      return await api.post("/api/jobs", { ...body, confirm_long: true });
    }
    throw err;
  }
}

function jobToast(res, r, enqueue, singer) {
  const name = shortTitle(res && res.song) || shortTitle(r);
  const who = singer ? ` — ${singer}` : "";
  if (!res.job && res.song && res.song.status === "ready") {
    toast(
      enqueue
        ? `“${name}” já estava pronta e entrou na fila${who}`
        : `“${name}” já está pronta na Biblioteca`,
    );
  } else if (enqueue) {
    toast(`Preparando “${name}” — entra na fila assim que ficar pronta${who}`);
  } else {
    toast(`Preparando “${name}” — acompanhe em Processamento`);
  }
}

/** "Só preparar" de um resultado (`lyricsMode` = override desta música ou null). */
export async function prepareOnly(r, btn, lyricsMode = null) {
  let confirmed = false;
  if (r.is_long) {
    confirmed = await confirmLong(r, r.duration_ms);
    if (!confirmed) return;
  }
  try {
    const res = await withBusy(
      btn,
      () => postJob(r, { enqueue: false, confirm_long: confirmed, lyricsMode }),
    );
    if (res) jobToast(res, r, false);
  } catch (err) {
    toastError(err);
  }
}

/** "Preparar e enfileirar" de um resultado (vídeo longo → confirma antes). */
export async function prepareAndEnqueue(r, lyricsMode = null) {
  let confirmed = false;
  if (r.is_long) {
    confirmed = await confirmLong(r, r.duration_ms);
    if (!confirmed) return;
  }
  const mode = { value: lyricsMode };
  askSinger({
    title: "Preparar e enfileirar",
    subtitle: [r.title, r.channel].filter(Boolean).join(" — "),
    confirmLabel: "Preparar e enfileirar",
    iconId: "mic",
    mode,
    submit: async (singer) => {
      const res = await postJob(r, {
        enqueue: true,
        singer,
        confirm_long: confirmed,
        lyricsMode: mode.value,
      });
      if (res) jobToast(res, r, true, singer);
    },
  });
}

/** "Adicionar à fila" de uma música já na biblioteca (pronta ou processando). */
export function enqueueSong(song) {
  askSinger({
    title: "Adicionar à fila",
    subtitle: [song.title, song.artist].filter(Boolean).join(" — "),
    confirmLabel: "Adicionar à fila",
    iconId: "plus",
    submit: async (singer) => {
      await api.post("/api/queue", {
        song_id: song.id || song.youtube_id,
        singer: singer || null,
        position: "end",
      });
      const who = singer ? ` — ${singer}` : "";
      toast(`“${shortTitle(song)}” adicionada à fila${who}`);
    },
  });
}

/**
 * Comando do player (contrato §9). Trata `stage_not_connected` mostrando o banner e um toast.
 * @returns {Promise<object|null>} PlayerState ou null em erro
 */
export async function command(action, value) {
  const body = { action, client_id: ctx.clientId };
  if (value !== undefined) body.value = value;
  try {
    return await api.post("/api/player/command", body);
  } catch (err) {
    if (err.code === "stage_not_connected") {
      ctx.onStageNotConnected();
      toast("O Palco não está com o som liberado. Abra o Palco e pressione Enter na TV.", {
        level: "warning",
      });
    } else {
      toastError(err);
    }
    return null;
  }
}
