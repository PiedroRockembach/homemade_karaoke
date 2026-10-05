/**
 * Formatação pt-BR: tempo m:ss, percentuais, offset, rótulos de etapas de job (contrato §5).
 *
 * Dono: frontend-engineer (T-007). Reutilizado pelo Controle (T-008).
 */

/** 63250 → "1:03"; ≥ 1 h → "1:12:40". Valores inválidos → "0:00". */
export function fmtTime(ms) {
  const total = Math.max(0, Math.floor((Number(ms) || 0) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** 0.8 → "80%" */
export function fmtPercent(v) {
  return Math.round((Number(v) || 0) * 100) + "%";
}

/** 120 → "+120 ms"; -50 → "−50 ms"; 0 → "0 ms" */
export function fmtOffset(ms) {
  const v = Math.round(Number(ms) || 0);
  if (v === 0) return "0 ms";
  return (v > 0 ? "+" : "−") + Math.abs(v) + " ms";
}

/** Segundos restantes aproximados: 28 → "cerca de 30 s"; 130 → "cerca de 2 min". */
export function fmtEta(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  if (seconds < 60) return `cerca de ${Math.max(5, Math.round(seconds / 5) * 5)} s`;
  return `cerca de ${Math.round(seconds / 60)} min`;
}

export const JOB_STAGE_LABELS = {
  queued: "Na fila",
  model_download: "Baixando modelo de IA",
  downloading: "Baixando",
  separating: "Separando voz",
  lyrics: "Buscando letra",
  finalizing: "Finalizando",
  done: "Pronta",
  error: "Erro",
  canceled: "Cancelado",
};

/** 16800 → "0:16.80" (centésimos; tempos do editor de sincronia). Negativo → "−0:01.20". */
export function fmtClock(ms) {
  const v = Math.round(Number(ms) || 0);
  const abs = Math.abs(v);
  const m = Math.floor(abs / 60000);
  const s = Math.floor((abs % 60000) / 1000);
  const cs = Math.floor((abs % 1000) / 10);
  return (v < 0 ? "−" : "") + `${m}:${String(s).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}

/** Rótulo da etapa; `lyrics` vira "Transcrevendo letra" (Whisper) ou "Sincronizando pela voz" (aligned). */
export function jobStageLabel(stage, lyricsSource = null) {
  if (stage === "lyrics" && lyricsSource === "whisper") return "Transcrevendo letra";
  if (stage === "lyrics" && lyricsSource === "aligned") return "Sincronizando pela voz";
  return JOB_STAGE_LABELS[stage] || stage || "";
}

/** "Título — Artista" ou só título. */
export function songLabel(song) {
  if (!song) return "";
  return song.artist ? `${song.title} — ${song.artist}` : song.title;
}

// ---------------------------------------------------------------- v0.3 modo convidado (DESIGN §12)

/** 0 → "Nenhum convidado conectado"; 1 → "1 convidado conectado"; 3 → "3 convidados conectados". */
export function fmtGuestCount(n) {
  const v = Math.max(0, Math.floor(Number(n) || 0));
  if (v === 0) return "Nenhum convidado conectado";
  return v === 1 ? "1 convidado conectado" : `${v} convidados conectados`;
}

/**
 * Opção do seletor de rede (GuestState.addresses[]), como no DESIGN §12.6:
 * `{address:"192.168.1.4", interface:"wlan0", kind:"wifi", recommended:true}` → "192.168.1.4 · wlan0 (recomendado)".
 * VPN ganha a marca "(VPN)" (listada, mas nunca recomendada).
 */
export function fmtAddress(a) {
  if (!a) return "";
  const notes = [a.kind === "vpn" ? "VPN" : "", a.recommended ? "recomendado" : ""].filter(Boolean);
  const base = a.interface ? `${a.address} · ${a.interface}` : String(a.address || "");
  return notes.length ? `${base} (${notes.join(", ")})` : base;
}

/** "Pedido por <nome>" só quando há nome de convidado e ele difere do cantor (DESIGN §12.5, AC-16.2). */
export function requestedByText(item, prefix = "Pedido por") {
  const name = item && typeof item.added_by_name === "string" ? item.added_by_name.trim() : "";
  if (!name) return "";
  const singer = item.singer ? String(item.singer).trim() : "";
  if (singer && singer.toLocaleLowerCase("pt-BR") === name.toLocaleLowerCase("pt-BR")) return "";
  return `${prefix} ${name}`;
}
