# Arquitetura — Karaokê local

> Ciclo `20261003-2339-karaoke` · Autor: architect · Base: [ADR-0001](adr/0001-stack.md) · [ADR-0002](adr/0002-guest-mode-readiness.md)
> Contratos: [api.md](contracts/api.md) · [lyrics-format.md](contracts/lyrics-format.md)

## 1. Visão geral

```
 Navegador (mesma máquina)                          Processo servidor: python -m karaoke  (127.0.0.1:8765)
┌────────────────────────────┐   REST (JSON)     ┌──────────────────────────────────────────────────────────┐
│ Controle  /                │──────────────────►│ FastAPI                                                  │
│  busca, jobs, biblioteca,  │◄──────────────────│  api/*  ── valida, chama serviços, devolve JSON          │
│  fila, mini-player, letras │   SSE /api/events │  EventBus ── publica eventos → todas as conexões SSE     │
└────────────────────────────┘◄─────────────┐    │  PlayerService ── estado único do player (fonte da verdade)
┌────────────────────────────┐              │    │  QueueService / LibraryService / LyricsService           │
│ Palco  /stage (TV, fullscr)│──── REST ────┼───►│  JobRunner (thread, fila serial, 1 job por vez)          │
│  AudioEngine (Web Audio)   │   comandos +  │    │    ├─ download  (yt-dlp + Deno, ffmpeg)  [in-process]    │
│  = DONO DO RELÓGIO         │   report 2 Hz │    │    ├─ separate  (demucs, torch)          [subprocesso]   │
│  LyricsRenderer (rAF)      │◄─── SSE ──────┘    │    ├─ lyrics    (LRCLIB via httpx)       [in-process]    │
└────────────────────────────┘                   │    └─ transcribe(faster-whisper)          [subprocesso]   │
        ▲  GET /media/... (Range)                │  SQLite (WAL)  data/karaoke.db                           │
        └────────────────────────────────────────│  Arquivos      data/songs/<id>/*.opus, thumb.jpg         │
                                                 └───────────────┬──────────────────────────────────────────┘
                                                                 │ rede (exceção explícita)
                                       YouTube (busca/download/thumbnails) · lrclib.net · HuggingFace (modelos, 1x)
```

Princípios:
- **Servidor = fonte única de estado** (fila, player, jobs, biblioteca). Os clientes só renderizam o que recebem
  e enviam comandos.
- **O Palco é o único que toca áudio** e é o **dono do relógio de reprodução**. O servidor guarda a *intenção*
  (tocar/pausar/seek) e a *última posição reportada*.
- Nenhum request externo parte do navegador (CSP `default-src 'self'`); thumbnails do YouTube passam por proxy do servidor.

## 2. Componentes do backend

| Módulo | Responsabilidade |
|--------|------------------|
| `karaoke/__main__.py` | Lê config, escolhe porta (fallback com `SO_REUSEADDR`), configura `HF_HOME`/`TORCH_HOME` → `data/models`, sobe uvicorn (`KaraokeServer`, ver §8.1), abre o Controle no navegador. |
| `karaoke/config.py` | Variáveis de ambiente e defaults (ver §8). Valida `KARAOKE_HOST` (só loopback na v1). |
| `karaoke/security.py` | **Ponto único de autorização** (§10): `Principal`, `authorize()` (Host, Origin, papel × escopo da rota), interface `RateLimiter` (no-op na v1). |
| `karaoke/app.py` | `create_app()`: rotas, estáticos, CSP, handlers de erro no formato do contrato. |
| `karaoke/db.py` | Conexão SQLite (WAL, `foreign_keys=ON`), migrações por `PRAGMA user_version`. |
| `karaoke/events.py` | `EventBus` assíncrono: `publish(type, data)`; cada conexão SSE tem uma `asyncio.Queue`. Thread-safe via `loop.call_soon_threadsafe` (o JobRunner roda em thread). |
| `karaoke/services/player.py` | `PlayerState`, aplica comandos, auto-avanço, timer da tela "A seguir", posse do áudio (stage claim). |
| `karaoke/services/queue.py` | CRUD/reordenação da fila, escolha do próximo item **pronto**. |
| `karaoke/services/library.py` | Músicas, exclusão (arquivos + linhas), busca local. |
| `karaoke/services/lyrics.py` | Candidatas, seleção, LRC manual, parse LRC/Enhanced LRC → formato JSON, offset. |
| `karaoke/youtube.py` | Busca (`ytsearchN:` com `extract_flat`), download de áudio, proxy/cache de thumbnails. |
| `karaoke/lrclib.py` | Cliente LRCLIB (`/api/get`, `/api/search`), limpeza de título, ranking por duração. |
| `karaoke/jobs/runner.py` | Fila serial persistida (tabela `jobs`); executa etapas; publica progresso; cancelamento; retomada. |
| `karaoke/jobs/worker.py` | Entrada do subprocesso de IA: `subprocess.Popen([python, -m, karaoke.jobs.worker, task.json])` com ambiente explícito (`cuda_env` + `model_env` + `HF_HUB_OFFLINE=1`). Progresso e resultado = **uma linha JSON por evento no stdout**. Cancelamento = `terminate()`; o worker sai sozinho se o pai morrer (watchdog de `getppid`). Ver ADR-0001, notas FIX-002. |
| `karaoke/system.py` | Detecção de GPU/CUDA, modelos baixados, ffmpeg, yt-dlp, Deno, espaço em disco. |

**Por que subprocesso para IA:** isola VRAM (liberada 100 % ao terminar), um crash nativo/OOM vira erro do job sem
derrubar o servidor, e cancelamento = `terminate()`. Custo: ~2–4 s de import/carga do modelo por etapa (aceitável).

## 3. Fluxo do job de processamento

```
POST /api/jobs ─► jobs(stage=queued) ─► JobRunner (1 por vez)
                                         │
  ┌──────────────────────────────────────┘
  ▼
[model_download]  só se modelo de separação (ou whisper, quando necessário) não está em data/models
  │               progresso = bytes baixados (huggingface_hub → data/models/hf; o demucs 4.1
  │               baixa de `adefossez/HTDemucs-ft` no HF, não do torch hub)
  ▼
[downloading]     yt-dlp bestaudio → data/tmp/<job>/source.<ext>   (progress_hooks → %)
  │               thumbnail → data/tmp/<job>/thumb.jpg
  │               ffmpeg → source.wav (44,1 kHz estéreo float)
  ▼
[separating]      subprocesso: demucs htdemucs_ft (cuda|cpu) → vocals.wav, instrumental.wav
  │               só o especialista de voz do bag (84 MB) e instrumental = mix − vocals
  │               (`full_bag=True` volta ao bag de 4); progresso pelo callback do apply_model
  ▼
[lyrics]          1) LRCLIB: limpa título → /api/get (artista, faixa, duração) → /api/search
  │                  candidatas com syncedLyrics; aceita a melhor se |duração − vídeo| ≤ 3 s
  │               2) senão: subprocesso faster-whisper sobre vocals.wav (ffmpeg decodifica para
  │                  float32 16 kHz mono → array numpy; o PyAV não é usado)
  │                  → linhas com palavras (timing "word")
  │               Falha de rede no LRCLIB ≠ erro do job: cai no Whisper.
  ▼
[finalizing]      ffmpeg wav → instrumental.opus, vocals.opus (libopus 160k)
  │               move atômico data/tmp/<job>/ → data/songs/<song_id>/   (rename no mesmo FS)
  │               transação SQLite: song.status=ready, lyrics, candidatas, job.stage=done
  ▼
[done] ─► eventos job.updated, library.updated; itens da fila com essa música ficam ready=true
```

- **Erro** em qualquer etapa: `job.stage=error`, `error={code,message}`, `song.status=error`; `data/tmp/<job>` apagado;
  nada aparece na biblioteca como pronto (só `status=ready` é listado como pronto).
- **Progresso global** (`job.progress`): pesos fixos — model_download 0–10 %, downloading 10–30 %, separating 30–80 %,
  lyrics 80–95 %, finalizing 95–100 %. Se não há model_download, a faixa é redistribuída proporcionalmente.
- **Retomada após reinício:** na inicialização, jobs em etapa não terminal voltam para `queued` (`attempts+1`) e o
  runner os executa de novo do início (o download é pulado se `source.wav` já existir em `data/tmp/<job>`).
  Após 3 tentativas falhas por reinício → `error` com código `interrupted`.
- **Job `transcribe`** (regerar letra com Whisper para música pronta): mesma fila serial; etapas `model_download?` →
  `lyrics` → `done`. Não altera o status `ready` da música durante a execução.
- **Cancelamento:** `DELETE /api/jobs/{id}` → queued: removido; em execução: termina o subprocesso / interrompe
  o yt-dlp no próximo hook, limpa tmp, `stage=canceled`.

## 4. Modelo de estado do player e sincronização

### 4.1 Estado (no servidor, persistido em `player_state`)

```jsonc
{
  "revision": 42,                    // incrementa a cada mudança
  "status": "playing",               // idle | waiting_gesture | loading | playing | paused | up_next | ended
  "queue_item_id": "q_7",            // item atual (null em idle)
  "song_id": "fJ9rUzIMcZQ",
  "position_ms": 63250,              // última posição conhecida
  "position_at": 1791167000123,      // epoch ms (Date.now) em que position_ms valia
  "duration_ms": 354000,
  "volume": 0.8,                     // 0..1 (curva perceptual aplicada no cliente: gain = v²)
  "guide_vocal": 0.0,                // 0..1  (voz guia)
  "offset_ms": 0,                    // offset da letra da música atual (espelho de songs.offset_ms)
  "autoplay": true,
  "up_next": null,                   // ou {queue_item_id, song_id, title, artist, singer, starts_at}
  "stage": { "connected": 1, "audio_owner": "c_ab12", "unlocked": true }
}
```

### 4.2 Quem é dono de quê
| Dado | Dono | Observação |
|------|------|------------|
| Intenção (tocar/pausar, item atual, volume, voz guia, offset, autoplay) | **Servidor** | Comandos de qualquer tela → `POST /api/player/command` |
| Relógio de áudio / posição real | **Palco (audio owner)** | `AudioContext.currentTime`; reporta 2 Hz e em todo evento |
| Avanço da fila / "A seguir" | **Servidor** | Ao receber `ended` do Palco; timer `starts_at` |

### 4.3 Sequência de um comando (≤ 300 ms; na prática < 30 ms local)
```
Controle            Servidor                         Palco (audio owner)
  │ POST command     │                                 │
  │ {action:pause}──►│ valida, status=paused, rev+1    │
  │◄── 200 state ────│── SSE player.state ────────────►│ engine.pause()
  │                  │◄── POST report {playing:false, position_ms} ─│
  │◄─ SSE player.position ───────────────────────────── │
```
Teclas no Palco seguem o **mesmo caminho** (Palco → `POST /api/player/command`), não alteram o áudio localmente
primeiro — isso mantém um único caminho de código. Exceção para responsividade: o HUD do Palco é exibido
imediatamente ao pressionar a tecla (otimista) e corrigido pelo `player.state`.

### 4.4 Posição exibida nos clientes
- **Palco**: lê direto do AudioEngine a cada `requestAnimationFrame` (não usa o valor do servidor).
- **Controle**: extrapola `pos = position_ms + (Date.now() - position_at)` se `status=playing` (mesma máquina →
  mesmo relógio de parede). Recebe `player.position` a ≤ 4 Hz para corrigir.

### 4.5 Posse do áudio (vários Palcos abertos)
- Cada aba gera `client_id` (em `sessionStorage`) e conecta em `/api/events?client_id=…&role=stage|control`.
- O áudio exige gesto: o Palco mostra "Pressione Enter para começar"; ao pressionar, chama `POST /api/stage/claim`
  → vira `audio_owner` (o último que reivindica vence; o anterior recebe `player.state` com outro owner e silencia,
  mostrando "Este palco está em modo espelho").
- `report` de quem não é owner é ignorado (204 mesmo assim).
- Owner desconecta (SSE cai por > 5 s) → servidor pausa (`status=paused`) e `stage.audio_owner=null`; o Controle mostra
  "Palco desconectado".
- Sem Palco conectado, `play` responde 409 `stage_not_connected` (o Controle oferece "Abrir Palco").

### 4.6 Ciclo de uma música
```
idle ──(fila recebe item pronto / command start)──► loading
loading ──(Palco: report event=loaded)──► playing      (servidor manda tocar se intenção = play)
playing ⇄ paused
playing ──(Palco: report event=ended)──► servidor marca item done ──► próximo item pronto?
        ├─ sim ──► up_next {starts_at = now + countdown (padrão 8 s)} ── autoplay? timer ──► loading (próximo)
        │                                                    └─ autoplay=false: espera command play
        └─ não ──► idle (Palco: tela de espera)
```
- Durante `up_next` o Palco já pré-carrega/decodifica a próxima música.
- `prev`: se posição > 3 s → reinicia; senão volta ao item `done` anterior.
- `next` em `playing`: marca atual `skipped` e segue o mesmo caminho de `ended` (sem countdown: vai direto a `loading`).

## 5. Áudio no Palco (AudioEngine) e voz guia

```
 instrumental.opus ─fetch─► decodeAudioData ─► AudioBuffer ─► BufferSource ─► GainNode(inst=1) ─┐
                                                                                                 ├─► GainNode(master = volume²) ─► destination
 vocals.opus ──────fetch─► decodeAudioData ─► AudioBuffer ─► BufferSource ─► GainNode(guide)  ──┘
```
- Os dois `AudioBufferSourceNode` recebem `start(when, offsetSec)` com o **mesmo `when`** → amostra-a-amostra alinhados,
  sem drift (diferente de dois `<audio>`).
- **Posição**: `pos = offsetSec + (ctx.currentTime - startedAtCtxTime)`; em pausa, `pos` congelado.
- **Seek**: para e descarta as fontes, cria novas e `start(now+0.02, pos)`. Instantâneo (buffers em memória).
- **Voz guia / volume sem cliques**: `gain.setTargetAtTime(v, ctx.currentTime, 0.03)`.
- **Latência de saída**: o tempo usado para a letra é `pos − (ctx.outputLatency || ctx.baseLatency || 0)`.
- **Tempo da letra** (ver lyrics-format §4): `t_letra = pos_ms − latência_ms − offset_ms`.
- Memória: ~85 MB por stem de 4 min (float32 estéreo 44,1 kHz) → ~170 MB por música; no máximo 2 músicas decodificadas
  (atual + próxima). Descartar buffers ao trocar.
- Fim da música: evento `ended` da fonte instrumental → `report {event:"ended"}`.
- O Palco também aplica **Fullscreen API** (tecla `F`) e **Screen Wake Lock** (se disponível) durante a reprodução.

## 6. Estrutura de pastas (projeto)

```
projects/karaoke/
├── Makefile                 install | start | dev | test | lint | fmt | doctor | update-ytdlp
├── pyproject.toml           deps (exceto torch), extras: dev, e2e; config do ruff e pytest
├── constraints.txt          torch==2.13.0 (fixa a build CUDA 12.9)
├── scripts/install.sh       cria .venv, escolhe índice do torch (cu129|cpu), pip install -e .[dev]
├── deno.json                config de deno lint/fmt para web/
├── karaoke/                 pacote Python (backend)
│   ├── __main__.py  app.py  config.py  security.py  db.py  events.py  system.py  youtube.py  lrclib.py  errors.py
│   │   deps.py (dependências dos routers)  doctor.py (`make doctor`)
│   ├── api/                 search.py jobs.py songs.py lyrics.py queue.py player.py media.py system.py settings.py events.py
│   ├── services/            player.py queue.py library.py lyrics.py
│   ├── jobs/                runner.py worker.py separate.py transcribe.py
│   ├── lyrics/              lrc_parser.py builder.py titleclean.py
│   └── web/                 ← UI servida como está (sem build)
│       ├── index.html       Controle
│       ├── stage.html       Palco
│       ├── css/             tokens.css (copiado de design/) base.css control.css stage.css
│       ├── fonts/           fontes vendorizadas
│       └── js/
│           ├── shared/      api.js (fetch + erros) events.js (EventSource + reconexão) state.js format.js
│           │   └── components/  search-results.js queue-list.js song-card.js (reutilizáveis pela futura /guest)
│           ├── control/     main.js search.js jobs.js library.js queue.js miniplayer.js lyrics-panel.js
│           └── stage/       main.js audio-engine.js lyrics-renderer.js hud.js keys.js screens.js
├── tests/                   unit/ api/ (TestClient, sem rede/GPU) e2e/ (Playwright) fixtures/ (wav curto, LRC)
├── design/                  tokens.css, prototype.html (ux-designer)
├── docs/                    ADR, ARCHITECTURE, contracts, DESIGN
└── data/                    (gitignored) dados do usuário — §7
```

## 7. Diretório de dados

Padrão `projects/karaoke/data/` (relativo ao projeto); sobrescrevível por `KARAOKE_DATA_DIR`.

```
data/
├── karaoke.db               SQLite (WAL): songs, lyrics_candidates, jobs, queue_items, player_state, settings
├── songs/<song_id>/         <song_id> = ID do vídeo do YouTube (11 chars) — dedup natural
│   ├── instrumental.opus    Ogg Opus 160 kbps estéreo 48 kHz
│   ├── vocals.opus
│   └── thumb.jpg            thumbnail (máx. 1280 px)
├── tmp/<job_id>/            trabalho em andamento (apagado ao concluir/falhar)
├── cache/thumbs/<yt_id>.jpg cache do proxy de thumbnails da busca (LRU, ≤ 200 MB)
├── models/                  HF_HOME e TORCH_HOME apontam aqui; modelos (demucs e faster-whisper) em models/hf/
├── runner.lock              flock do JobRunner: só 1 processo por data dir processa jobs
└── logs/karaoke.log         log rotativo
```
O original baixado não é mantido (instrumental + voz ≈ original); ~10 MB por música.

### Tabelas (resumo)
| Tabela | Colunas principais |
|--------|-------------------|
| `songs` | id (yt id) PK, title, artist, channel, duration_ms, status, error_code, lyrics_candidate_id, offset_ms, size_bytes, created_at, updated_at |
| `lyrics_candidates` | id PK (`lrclib:<n>` · `whisper:<n>` · `manual:<n>`), song_id FK, source, meta_json, lyrics_json, created_at |
| `jobs` | id PK, kind (process/transcribe), song_id FK, stage, stage_progress, progress, error_code, error_message, attempts, created_at, updated_at |
| `queue_items` | id PK, song_id FK, singer, status (pending/playing/done/skipped), sort_key REAL, added_by TEXT NOT NULL DEFAULT 'host', added_at |
| `player_state` | linha única: json do estado + revision |
| `settings` | key PK, value_json |

## 8. Configuração e execução

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `KARAOKE_PORT` | `8765` | Se ocupada, tenta as próximas 10 portas; a URL final é impressa e aberta. |
| `KARAOKE_HOST` | `127.0.0.1` | Só loopback na v1 (outro valor aborta com mensagem clara). LAN = opt-in futuro com `KARAOKE_GUEST_MODE=1` (ADR-0002). |
| `KARAOKE_DATA_DIR` | `./data` | Diretório de dados. |
| `KARAOKE_DEVICE` | `auto` | `auto` \| `cuda` \| `cpu`. |
| `KARAOKE_SEPARATION_MODEL` | `htdemucs_ft` | ou `htdemucs` (mais rápido). |
| `KARAOKE_WHISPER_MODEL` | `large-v3-turbo` (GPU) / `small` (CPU) | |
| `KARAOKE_NO_BROWSER` | — | `1` = não abrir o navegador. |
| `KARAOKE_TORCH_INDEX` | auto | Força o índice do torch na instalação. |

Comandos (`Makefile`):
| Comando | Faz |
|---------|-----|
| `make install` | `scripts/install.sh` (venv, torch cu129/cpu, deps, `deno` via yt-dlp extra) |
| `make start` | `.venv/bin/python -m karaoke` (abre `http://127.0.0.1:8765/`) |
| `make dev` | `uvicorn karaoke.app:create_app --factory --reload --host 127.0.0.1 --port 8765` |
| `make test` | `pytest -m "not gpu and not network and not e2e"` |
| `make lint` / `make fmt` | `ruff check` + `ruff format`; `deno lint` + `deno fmt` em `karaoke/web/` |
| `make doctor` | imprime o resultado de `GET /api/system` sem subir o servidor (GPU, CUDA, ffmpeg, Deno) |
| `make update-ytdlp` | `pip install -U "yt-dlp[default,deno]"` |

### 8.1 Encerramento (Ctrl+C / SIGTERM)

O Palco e o Controle mantêm streams SSE abertos o tempo todo, e o uvicorn só encerra quando as conexões fecham.
Por isso `__main__.py` usa um `uvicorn.Server` próprio (`KaraokeServer`):

1. **1º sinal** (SIGINT ou SIGTERM): `app.state.bus.close_all()` roda no loop na mesma hora. Cada `sse_stream`
   recebe `None` e retorna, as respostas terminam e o uvicorn fecha as conexões (em geral < 0,5 s). O que sobrar
   é cancelado depois de `timeout_graceful_shutdown = 3 s`. Em seguida vem o lifespan shutdown (ordem inversa do
   startup): `jobs.runner.shutdown` mata o worker (o job volta a `queued` na próxima inicialização) e
   `services.player.shutdown` grava a posição.
2. **2º sinal** (qualquer um) ou **watchdog de 10 s** sem terminar: `hard_exit` chama `runner.stop(timeout=2)`
   (mata o worker) e sai com `os._exit(128+sinal)`.

Contrato com `events.py`: `EventBus.close_all()` é idempotente, pode ser chamado no loop do servidor e faz todo
`sse_stream` ativo terminar. O lifespan chama `close_all()` de novo, sem efeito. O EventSource dos clientes
reconecta sozinho (retry 1 s) quando o app volta.

Porta: `pick_port` testa o bind com `SO_REUSEADDR` (POSIX), igual ao bind do asyncio/uvicorn. Conexões em
TIME_WAIT da execução anterior não fazem o reinício cair para 8766. Só uma porta com servidor escutando conta
como ocupada.

Testes: marcadores `gpu` e `network` para testes que precisam de GPU/rede (fora do `make test` padrão). Os serviços de
IA/YouTube/LRCLIB ficam atrás de interfaces simples para serem substituídos por fakes nos testes de API.

## 9. Segurança e privacidade
- Bind só em loopback; checagem de `Host` header (`127.0.0.1:<porta>`, `localhost:<porta>`, `[::1]:<porta>`) contra DNS
  rebinding e de `Origin` em mutações contra CSRF — ambos em `security.authorize` (§10).
- CSP: `default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'`.
- `youtube_id` validado por regex `^[A-Za-z0-9_-]{11}$` antes de virar caminho de arquivo; nenhum caminho vem do cliente.
- yt-dlp chamado via API Python (sem shell); ffmpeg via `subprocess` com lista de argumentos.
- Sem CORS: nenhum header `Access-Control-*` (tudo é same-origin).

## 10. Autorização e preparação para o modo convidado ([ADR-0002](adr/0002-guest-mode-readiness.md))

O modo convidado (LAN + QR, F14) **não** faz parte da v1; o esqueleto só precisa deixar os ganchos abaixo.

**Config de host** — `config.py` expõe `host`, `port`, `allowed_hosts` (derivado: `127.0.0.1`, `localhost`, `[::1]`
com a porta efetiva, calculada **depois** do fallback de porta) e `guest_mode=False` (fixo na v1). `__main__.py` passa
`host`/`port` ao uvicorn; nada mais no código lê variáveis de ambiente de rede.

**Autorização em um só lugar** — `app.py` registra `Depends(security.authorize)` como dependência **global** do
FastAPI. Cada router/rota declara seu escopo com um marcador único, por exemplo
`APIRouter(dependencies=[scope("host")])` ou `@router.get(..., dependencies=[scope("guest-safe")])`, conforme a
tabela de [api.md §0.1](contracts/api.md). `authorize` lê o escopo da rota (`request.scope["route"]`), resolve o
`Principal` e decide; rotas sem marcador valem como `host`. Os handlers recebem `principal: Principal = Depends(...)`
só quando precisam do id (ex.: `POST /api/queue` grava `added_by=principal.id`). Nenhum handler compara papéis.

```
requisição ─► authorize ─┬─ Host permitido? ─── não ─► 403 forbidden
                         ├─ mutação: Origin ok? JSON? ── não ─► 403 / 400
                         ├─ loopback ─► Principal(host)          [futuro: cookie guest ─► Principal(guest)]
                         ├─ escopo da rota × papel ── guest em host ─► 403
                         └─ RateLimiter.check (no-op v1) ─► handler
```

**Testes-gancho da v1** (`tests/api/test_security.py`): toda rota `/api/*` e `/media/*` declara escopo; `Host: evil.com`
→ 403; `POST` com `Origin: http://evil.com` → 403; `POST /api/queue` grava `added_by="host"` mesmo se o corpo tentar
outro valor.

**Frontend** — só URLs relativas; componentes de busca/fila/cartão de música em `js/shared/components/` recebem
dados e callbacks (sem depender do layout do Controle) e páginas funcionam a partir de 360 px, para que a futura
`/guest` os reutilize. `EventBus` mantém a assinatura `publish(type, data)`; o filtro por principal entra depois como
`visible_to(principal, event)` (na v1: sempre `True`).

**Não fazer na v1:** rotas `/api/guest/*`, página `/guest`, geração de QR, cookies, rate limit real, bind não-loopback.

