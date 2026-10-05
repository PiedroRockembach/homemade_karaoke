/**
 * Store simples alimentado pelo `hello` e pelos eventos SSE (contrato §10).
 *
 * Forma do estado:
 *   { connection, clientId, serverSkewMs, player, queue, jobs: {[id]: Job}, settings, system }
 * `serverSkewMs` = server_time − Date.now() no último hello (mesma máquina → ~0; usado no "A seguir").
 *
 * Dono: frontend-engineer (T-007). Reutilizado pelo Controle (T-008).
 */

export function createStore(initial = {}) {
  let state = {
    connection: "connecting",
    clientId: null,
    serverSkewMs: 0,
    player: null,
    queue: null,
    jobs: {},
    settings: null,
    system: null,
    ...initial,
  };
  const subs = new Set();
  return {
    get: () => state,
    /** Mescla `patch` no estado e notifica assinantes com (state, changedKeys). */
    set(patch) {
      state = { ...state, ...patch };
      const keys = Object.keys(patch);
      for (const fn of subs) fn(state, keys);
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}

function replaceSong(item, song) {
  if (!item || !item.song || item.song.id !== song.id) return item;
  return { ...item, song: { ...item.song, ...song } };
}

/**
 * Aplica um evento SSE ao store. Retorna true se o estado mudou.
 * Eventos desatualizados (revision menor) são ignorados.
 */
export function applyEvent(store, type, data) {
  const s = store.get();
  switch (type) {
    case "hello": {
      const jobs = {};
      for (const j of data.jobs || []) jobs[j.id] = j;
      store.set({
        clientId: data.client_id || s.clientId,
        serverSkewMs: typeof data.server_time === "number" ? data.server_time - Date.now() : 0,
        player: data.player || null,
        queue: data.queue || null,
        jobs,
        settings: data.settings || null,
        system: data.system || null,
      });
      return true;
    }
    case "player.state":
      if (s.player && typeof s.player.revision === "number" && data.revision < s.player.revision) {
        return false;
      }
      store.set({ player: data });
      return true;
    case "player.position": {
      const p = s.player;
      if (!p || p.queue_item_id !== data.queue_item_id) return false;
      store.set({
        player: {
          ...p,
          position_ms: data.position_ms,
          position_at: data.position_at,
          _playing: data.playing,
        },
      });
      return true;
    }
    case "queue.updated":
      if (s.queue && typeof s.queue.revision === "number" && data.revision < s.queue.revision) return false;
      store.set({ queue: data });
      return true;
    case "job.updated":
      store.set({ jobs: { ...s.jobs, [data.id]: data } });
      return true;
    case "job.removed": {
      if (!s.jobs[data.id]) return false;
      const jobs = { ...s.jobs };
      delete jobs[data.id];
      store.set({ jobs });
      return true;
    }
    case "song.updated": {
      const patch = {};
      if (s.player && s.player.song && s.player.song.id === data.id) {
        patch.player = { ...s.player, song: { ...s.player.song, ...data } };
      }
      if (s.queue) {
        patch.queue = {
          ...s.queue,
          current: replaceSong(s.queue.current, data),
          items: (s.queue.items || []).map((it) => replaceSong(it, data)),
        };
      }
      store.set(patch);
      return true;
    }
    case "settings.updated":
      store.set({ settings: data });
      return true;
    case "system.updated":
      store.set({ system: { ...(s.system || {}), ...data } });
      return true;
    default:
      return false;
  }
}

/** Jobs ordenados: em execução, fila, terminados (mais recentes primeiro). */
export function jobList(state) {
  const rank = (j) => (j.queue_position === 0 ? 0 : j.queue_position != null ? 1 : 2);
  return Object.values(state.jobs || {}).sort((a, b) =>
    rank(a) - rank(b) || (a.queue_position ?? 0) - (b.queue_position ?? 0) ||
    String(b.updated_at).localeCompare(String(a.updated_at))
  );
}

/**
 * Posição estimada do player em ms a partir do servidor (Controle e Palco espelho):
 * `position_ms + (agora − position_at)` quando tocando.
 */
export function extrapolatePosition(player, skewMs = 0) {
  if (!player) return 0;
  const playing = player._playing ?? player.status === "playing";
  let pos = player.position_ms || 0;
  if (playing && player.status === "playing" && player.position_at) {
    pos += Date.now() + skewMs - player.position_at;
  }
  if (player.duration_ms) pos = Math.min(pos, player.duration_ms);
  return Math.max(0, pos);
}
