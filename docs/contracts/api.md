# Contrato da API — Karaokê local (v1)

> Dono: architect · Ciclo `20261003-2339-karaoke` · Ver também [lyrics-format.md](lyrics-format.md) e
> [ARCHITECTURE.md](../ARCHITECTURE.md). Mudanças só via `CHANGE_REQUEST` ao orchestrator.
>
> **v0.2** (ciclo `20261004-1742-karaoke`, T-001, backend-engineer por delegação da tarefa): setting `lyrics_mode`,
> override `lyrics_mode` em `POST /api/jobs`, jobs `align`/`analyze`, origem `aligned`, `label`/`derived_from` nas
> candidatas, rotas `/lyrics/align`, `/lyrics/manual-timing`, `/lyrics/detect-offset`, evento `lyrics.offset_detected`.
> **CR-001** (T-006): texto sempre da letra oficial — `transcribe` vira alias de `aligned`, origem `online_plain`
> (LRCLIB só texto), colar texto puro, `/align` busca a letra antes, `official_text`/`selectable` nas candidatas.
> **v0.3** (ciclo `20261005-0109-karaoke`, T-001, architect): **modo convidado** — [ADR-0003](../adr/0003-guest-mode.md).
> Escopo `public`, papéis `guest`/`anon`, rotas `/guest` e `/api/guest/*` (§12), restrições de convidado em cada rota
> `guest-safe`, `QueueItem.added_by_name`, `Job.requested_by`, `up_next.added_by_name`, eventos `guest.state` e
> `guest.ended`, `role=guest` no SSE, erros 401/409/429 novos.

## 0. Convenções

- Base: `http://127.0.0.1:<porta>` (padrão 8765). Mesma origem para UI, API e mídia. Com o modo convidado ligado
  (v0.3), o **mesmo app** também responde em `http://<ip-lan>:<porta>` — só para convidados (ADR-0003 §1).
- JSON UTF-8; nomes em `snake_case`; tempos em **milissegundos inteiros** (`*_ms`); datas ISO-8601 UTC
  (`"2026-10-03T23:50:12Z"`); valores normalizados `0..1` como float.
- IDs: música = ID do vídeo do YouTube (`^[A-Za-z0-9_-]{11}$`); job `j_<n>`; item de fila `q_<n>`;
  candidata de letra `lrclib:<n>` | `whisper:<n>` | `manual:<n>`; cliente `c_<aleatório>`.
- Mensagens humanas (`message`) vêm em pt-BR prontas para exibir; a UI deve decidir por `code`.
- Toda resposta de mutação devolve o recurso atualizado **e** o servidor publica o evento SSE correspondente
  (a UI pode confiar só nos eventos para manter o estado; a resposta serve para feedback imediato).
- O frontend usa **somente URLs relativas** (`/api/...`, `/media/...`) — nunca `http://127.0.0.1:...` no código.
- Mutações (`POST`/`PUT`/`PATCH`/`DELETE`) com corpo enviam `Content-Type: application/json`; o servidor recusa
  `Origin` de outra origem (ver §0.1).

### 0.1 Autorização e escopo das rotas (ver [ADR-0002](../adr/0002-guest-mode-readiness.md))

Toda requisição passa por **um único ponto de autorização** (`karaoke/security.py › authorize`, dependência global
do app) que, nesta ordem: valida o `Host` header, valida `Origin` em mutações, resolve o *principal* e compara o papel
com o **escopo** declarado pela rota. Nenhuma rota faz checagem própria de papel.

- Escopos (v0.3): **`host`** (controle total) · **`guest-safe`** (host ou convidado com sessão, com as restrições
  anotadas) · **`public`** (qualquer cliente que alcance o servidor — inclusive convidado ainda sem sessão).
  **Deny-by-default:** rota sem escopo declarado = `host` (um teste garante que toda rota declara escopo).
- Papéis: **`host`** = cliente em loopback (nunca por token/cookie). **`guest`** = conexão pelo listener da LAN, modo
  ligado e cookie `karaoke_guest` válido → id `g_<n>`. **`anon`** = conexão pelo listener da LAN, modo ligado, sem
  sessão válida (só alcança `public`). Com o modo desligado não há listener na LAN (conexão recusada); requisição
  não-loopback que ainda chegue (drenagem) → `403 forbidden` (`reason: guest_mode_off`).
- Decisão por escopo: `host` exige host (senão `403 forbidden`); `guest-safe` exige host|guest (anon →
  `401 guest_session_required`); `public` aceita todos. Depois, rate limit de convidado/anon (`429 rate_limited`).
- Hosts permitidos: `127.0.0.1:<porta>`, `localhost:<porta>`, `[::1]:<porta>` **e**, só com o modo ligado,
  `<ip-lan>:<porta>`. Origens permitidas: as mesmas, com `http://`.

| Escopo | Rotas |
|--------|-------|
| `host` | `GET /`, `GET /stage`, mídia de áudio (`instrumental.opus`, `vocals.opus`), `GET /api/system`, `POST /api/system/models/*`, `/api/settings` (GET/PATCH), `GET /api/jobs` (lista), `POST /api/jobs/{id}/retry`, `DELETE /api/jobs/{id}`, `PATCH`/`DELETE /api/songs/{id}`, todas as rotas de letras (§7), `PATCH /api/queue/{id}`, `DELETE /api/queue`, `POST /api/queue/reorder`, `POST /api/queue/{id}/move`, `POST /api/player/command`, `POST /api/player/report`, `POST /api/stage/claim`, `GET /api/guest/state`, `POST /api/guest/{enable,disable,rotate,end-sessions}`, `GET /api/guest/qr` |
| `guest-safe` | thumbnails (`/media/songs/{id}/thumb.jpg`, `/api/thumbs/{id}`), `GET /api/health`, `GET /api/search`¹, `POST /api/jobs`², `GET /api/jobs/{id}`³, `GET /api/songs`, `GET /api/songs/{id}`, `GET /api/queue`, `POST /api/queue`⁴, `DELETE /api/queue/{id}`⁵ (v0.3: era `host`), `GET /api/player`, `GET /api/events`⁶, `DELETE /api/guest/session` |
| `public` | `GET /guest`, `GET /static/*` (v0.3: era `guest-safe`), `POST /api/guest/session`, `GET /api/guest/session` |

Restrições de convidado (v0.3, ADR-0003 §5; para o host nada muda):
¹ balde `search`: 30 por minuto (rajada/capacidade 10; DECISION 0019) → `429 rate_limited`; resultados entram no cache do servidor usado por ².
² `enqueue` forçado `true`; `position` sempre fim; `title`/`channel`/`duration_ms`/`confirm_long`/`lyrics_mode` do corpo
**ignorados** (metadados apurados pelo servidor; modo de letra = setting do host); vídeo > `long_video_warn_s` →
`409 long_video_not_allowed`; sem cotas por convidado (DECISION 0016);
`requested_by` = id do convidado; `singer` padrão = nome do convidado.
³ só jobs com `requested_by` = o próprio convidado (outros → `404 job_not_found`).
⁴ `position` só `"end"` (outro valor → `400 invalid_request`); `singer` padrão = nome do convidado (≤ 40);
`added_by`/`added_by_name` forçados pelo servidor; sem cota de pedidos (DECISION 0016).
⁵ posse (§0.2) e só itens `pending` (o item tocando → `409 queue_item_playing`).
⁶ convidado só com `role=guest` (e `role=guest` só para convidado; senão `403 forbidden`); eventos filtrados (§10);
≤ 30 conexões de convidado no total e ≤ 3 por convidado → `429 rate_limited`.
Todas as demais requisições de convidado passam pelos baldes `read` (60, 120/min) e `write` (10, 20/min); anon por
IP: `anon` (60, 120/min) e `session` (10, 20/min, só `POST /api/guest/session`; DECISION 0016). `GET /guest` e `GET /static/*` usam
o balde próprio `static` (300, 600/min, por IP, convidado ou anon) e não gastam `read`/`anon` (FIX-001).

Cada seção abaixo repete o escopo na linha da rota como `[host]` ou `[guest-safe]`.

### 0.2 Regra de posse (SPEC AC-04.4 / AC-05.5 / AC-16.1, DECISION 0009 e 0006 do ciclo `20261005-0109`)

- **O convidado só remove o que adicionou**: `DELETE /api/queue/{id}` de convidado exige `QueueItem.added_by == ator`;
  caso contrário → `403 not_owner`.
- **O anfitrião modera** (DECISION 0006, que substitui a DECISION 0012 do ciclo `20261003-2339`): o host remove
  **qualquer** item da fila, inclusive os de convidados, e qualquer música da biblioteca (`DELETE /api/songs/{id}` é
  rota `host`). `can_remove` é `true` para o host em todo recurso com dono conhecido.
- Política única: `karaoke/security.py › can_remove(actor, resource)` / `ensure_can_remove(...)`. Escopo de rota
  (`host`/`guest-safe`) e posse são checagens independentes: a rota precisa passar nas duas.
- `added_by` é sempre definido pelo servidor a partir do principal (`"host"` ou `"g_<n>"`).
  `Song.added_by` = ator que criou o primeiro job da música — **exceto convidado**: música preparada por convidado
  fica com `added_by = "host"` (a biblioteca é do anfitrião; ADR-0003 §6).
- As respostas trazem `can_remove: bool` calculado **para o ator da requisição** (em eventos SSE, para o ator da
  conexão). A UI esconde "remover" quando `can_remove=false`.
- Não são remoção (seguem só o escopo `host`): `next`/pular, `start`, `DELETE /api/queue` (limpar), `reorder`/`move`.

### Formato de erro (todas as rotas)
```json
HTTP 404
{ "error": { "code": "song_not_found", "message": "Música não encontrada.", "details": { "song_id": "abc" } } }
```

| HTTP | `code` | Quando |
|------|--------|--------|
| 400 | `invalid_request` | Payload malformado / campo inválido (`details.fields`); mutação com corpo sem `Content-Type: application/json` |
| 401 | `guest_session_required` | v0.3 — convidado sem sessão válida (cookie ausente, expirado ou encerrado) em rota `guest-safe` |
| 401 | `guest_invite_invalid` | v0.3 — `POST /api/guest/session` com token inválido, expirado ou rotacionado (AC-13.3) |
| 403 | `forbidden` | Recusado pelo ponto único de autorização: `Host` não permitido, `Origin` de outra origem em mutação, cliente não-loopback com modo desligado (`details.reason: "guest_mode_off"`), convidado em rota `host` (`"host_only"`), `role` de SSE incompatível com o papel |
| 403 | `not_owner` | v0.3 — **convidado** removendo item da fila que não adicionou (`added_by` ≠ ator atual). O host modera e remove qualquer item/música (DECISION 0006) — regra de posse §0.2 |
| 404 | `song_not_found`, `job_not_found`, `queue_item_not_found`, `candidate_not_found`, `model_not_found` | |
| 409 | `long_video_confirmation_required` | Vídeo > 15 min sem `confirm_long: true` (host) |
| 409 | `long_video_not_allowed` | v0.3 — convidado pediu vídeo > `long_video_warn_s` (`details.duration_ms`, `details.max_ms`); não há confirmação |
| 409 | `guest_quota_exceeded` | **Removido** (DECISION 0016): o servidor não emite mais; não há cotas por convidado |
| 409 | `queue_item_playing` | v0.3 — convidado tentou remover o próprio item que está tocando |
| 409 | `song_queued_by_others` | v0.3 — `DELETE /api/songs/{id}` com itens pendentes de outros atores (`details.count`), checado na mesma transação da exclusão |
| 413 | `payload_too_large` | v0.3 — corpo da requisição acima do limite (`details.max_bytes`): 16 KiB fora do loopback, 8 MiB no loopback. Recusado antes de ler o corpo (por `Content-Length`) ou ao passar do limite (chunked), antes da autorização |
| 409 | `guest_mode_off` | v0.3 — `rotate`/`end-sessions`/`qr` com o modo desligado |
| 409 | `lan_unavailable` | v0.3 — `enable` sem nenhum IPv4 de LAN utilizável |
| 409 | `lan_bind_failed` | v0.3 — não foi possível escutar em `<ip>:<porta>` (`details.address`, `details.port`, `details.reason`) |
| 409 | `song_not_ready` | Ação exige música pronta |
| 409 | `song_in_use` | Excluir música que está tocando |
| 409 | `stage_not_connected` | `play`/`start` sem Palco dono do áudio |
| 409 | `job_not_retryable` | Retry de job que não está em `error`/`canceled` |
| 409 | `stale_queue` | `reorder` com `revision` desatualizada |
| 409 | `lyrics_text_required` | v0.2 — `/align` com `candidate_id` de uma candidata `whisper` (texto não oficial) |
| 409 | `candidate_not_selectable` | CR-001 — selecionar candidata só texto (`timing: "none"`: `online_plain` ou colada sem tempos) |
| 409 | `no_lyrics` | v0.2 — "Detectar offset" em música sem letra selecionada |
| 422 | `invalid_lrc` | LRC colado sem nenhuma linha com tempo (`details.line`) |
| 422 | `invalid_timing` | v0.2 — tempos do editor inválidos (`details.line` 0-based, `details.reason`, ver §7) |
| 502 | `network_unavailable` | Sem internet (busca, download, LRCLIB) |
| 502 | `youtube_error` | yt-dlp falhou (`details.hint` sugere `make update-ytdlp` quando aplicável) |
| 500 | `internal_error` | Inesperado (detalhe no log) |
| 501 | `not_implemented` | **Só durante o BUILD:** rota ainda em stub no esqueleto (`details.feature`). Some quando a rota é implementada |
| 429 | `rate_limited` | v0.3 — limite de convidado/anon (`details.retry_after_s`, `details.reason`: `search` \| `read` \| `write` \| `session` \| `anon` \| `static` \| `sse_limit` \| `too_many_guests`); header `Retry-After` |

Códigos de erro **de job** (campo `job.error.code`): `network_unavailable`, `youtube_error`, `video_unavailable`,
`download_failed`, `ffmpeg_failed`, `model_download_failed`, `separation_failed`, `gpu_out_of_memory`,
`transcription_failed`, `disk_full`, `interrupted`, `canceled`, `internal_error`, `align_failed` (v0.2: a voz não
canta o texto escolhido / a letra de origem foi apagada), `video_too_long` (v0.3: job de convidado cuja duração real
passou de `options.max_duration_ms` — defesa em profundidade).

---

## 1. Páginas e arquivos estáticos

| Método | Rota | Resposta |
|--------|------|----------|
| GET | `/` | `index.html` (Controle) · `[host]` |
| GET | `/stage` | `stage.html` (Palco) · `[host]` |
| GET | `/guest` | `guest.html` (página do convidado, mobile-first) · `[public]` (v0.3) |
| GET | `/static/{path}` | arquivos de `karaoke/web/` (css, js, fonts) · `[public]` (v0.3; era `guest-safe`) |

## 2. Mídia

Todas suportam `Range` (206) e `ETag`; `Cache-Control: private, max-age=31536000, immutable` (arquivos nunca mudam
para o mesmo `song_id`; reprocessar gera novo `?v=` — ver `Song.media.version`).

| Método | Rota | Content-Type |
|--------|------|--------------|
| GET | `/media/songs/{song_id}/instrumental.opus` | `audio/ogg` · `[host]` |
| GET | `/media/songs/{song_id}/vocals.opus` | `audio/ogg` · `[host]` |
| GET | `/media/songs/{song_id}/thumb.jpg` | `image/jpeg` · `[guest-safe]` |
| GET | `/api/thumbs/{youtube_id}` | `image/jpeg` — proxy/cache da thumbnail do YouTube para resultados de busca (404 se indisponível; UI usa placeholder) · `[guest-safe]` |

## 3. Sistema

### GET `/api/health` `[guest-safe]`
```json
200 { "ok": true, "version": "0.3.0" }
```

### GET `/api/system` `[host]`
```json
200
{
  "device": "cuda",                      // "cuda" | "cpu" — o que será usado de fato
  "gpu": { "available": true, "name": "NVIDIA GeForce RTX 5060", "vram_total_mb": 8151,
           "compute_capability": "12.0", "torch": "2.13.0+cu129", "cuda": "12.9", "warning": null },
  "models": [
    { "id": "htdemucs_ft", "kind": "separation", "downloaded": true,  "size_mb": 84,   "active": true },
    { "id": "large-v3-turbo", "kind": "transcription", "downloaded": false, "size_mb": 1620, "active": true }
  ],
  "model_download": null,                // ou { "model_id": "...", "progress": 0.42 } durante download
  "tools": { "ffmpeg": { "ok": true, "version": "n9.0.2" },
             "yt_dlp": { "ok": true, "version": "2026.8.19" },
             "js_runtime": { "ok": true, "name": "deno", "version": "2.9.7" } },
  "data_dir": "data",                    // relativo ao projeto quando possível
  "disk_free_mb": 182340,
  "library": { "songs": 12, "size_mb": 131 }
}
```
`gpu.warning` (string pt-BR) quando a GPU existe mas não pôde ser usada (ex.: "CUDA indisponível — usando CPU, o processamento será mais lento").

### POST `/api/system/models/{model_id}/download` `[host]`
Pré-baixa um modelo fora de um job. `202 { "model_id": "large-v3-turbo", "status": "downloading" }`; progresso via
evento `system.updated`. `200` se já baixado. `404 model_not_found`.

### GET `/api/settings` · PATCH `/api/settings` `[host]`
```json
200
{
  "countdown_s": 8,               // duração da tela "A seguir" (0–30)
  "autoplay": true,
  "guide_vocal_default": 0.0,     // valor inicial da voz guia (persistido quando o usuário muda o slider)
  "volume": 0.8,
  "separation_model": "htdemucs_ft",   // "htdemucs_ft" | "htdemucs"
  "whisper_model": "large-v3-turbo",   // "large-v3-turbo" | "medium" | "small"
  "long_video_warn_s": 900,
  "theme": "system",              // Controle: "system" | "light" | "dark"
  "lyrics_mode": "aligned"        // "aligned" (letra oficial sincronizada pela voz) | "online" (LRCLIB com tempos como está).
                                  // CR-001: PATCH aceita "transcribe" (alias legado) e grava/devolve "aligned". A UI mostra só os 2.
}
```
PATCH aceita qualquer subconjunto → 200 com o objeto completo. Evento `settings.updated`.

## 4. Busca no YouTube

### GET `/api/search?q={texto}&limit={1..25, padrão 15}` `[guest-safe]`
```json
200
{
  "query": "bohemian rhapsody",
  "results": [
    {
      "youtube_id": "fJ9rUzIMcZQ",
      "title": "Queen – Bohemian Rhapsody (Official Video Remastered)",
      "channel": "Queen Official",
      "duration_ms": 359000,
      "thumbnail": "/api/thumbs/fJ9rUzIMcZQ",
      "url": "https://www.youtube.com/watch?v=fJ9rUzIMcZQ",
      "is_long": false,                 // duração > long_video_warn_s
      "is_live": false,                 // lives não podem ser processadas (UI desabilita)
      "library": { "status": "ready", "job_id": null }   // null se não está na biblioteca
    }
  ]
}
```
`library.status`: `ready` | `processing` (job ativo, `job_id` preenchido) | `error`.
Erros: `400 invalid_request` (q vazio ou > 200 chars), `502 network_unavailable`, `502 youtube_error`.
Meta de latência: ≤ 5 s (usa `extract_flat`).

## 5. Jobs (processamento)

### Objeto `Job`
```json
{
  "id": "j_31",
  "kind": "process",                  // "process" | "transcribe" | "align" (v0.2) | "analyze" (v0.2, detectar offset)
  "song_id": "fJ9rUzIMcZQ",
  "song": { "title": "Bohemian Rhapsody", "artist": "Queen", "thumbnail": "/api/thumbs/fJ9rUzIMcZQ" },
  "stage": "separating",              // queued | model_download | downloading | separating | lyrics | finalizing | done | error | canceled
  "stage_progress": 0.55,             // 0..1 ou null (indeterminado)
  "progress": 0.575,                  // 0..1 global (pesos em ARCHITECTURE §3)
  "queue_position": 0,                // 0 = executando; 1.. = posição na fila de processamento; null se terminal
  "device": "cuda",
  "lyrics_source": null,              // "lrclib" | "whisper" | "aligned" (v0.2); em process/align já vem "aligned"/"whisper" durante a etapa lyrics
  "error": null,                      // ou { "code": "youtube_error", "message": "Não foi possível baixar o vídeo." }
  "attempts": 1,
  "requested_by": "host",             // v0.3: "host" | "g_<n>" (quem pediu o preparo)
  "created_at": "2026-10-03T23:50:12Z",
  "updated_at": "2026-10-03T23:50:40Z"
}
```
Rótulos pt-BR sugeridos para `stage`: queued "Na fila", model_download "Baixando modelo de IA",
downloading "Baixando", separating "Separando voz", lyrics "Buscando letra" (ou "Transcrevendo letra" quando
`lyrics_source` vier `whisper`, "Sincronizando pela voz" quando vier `aligned`), finalizing "Finalizando", done "Pronta", error "Erro", canceled "Cancelado".

### POST `/api/jobs` — criar job (Preparar / Preparar e enfileirar) `[guest-safe]¹`
```json
{ "youtube_id": "fJ9rUzIMcZQ",
  "title": "Queen – Bohemian Rhapsody (Official Video Remastered)",   // opcional (da busca; evita nova consulta)
  "channel": "Queen Official",                                        // opcional
  "duration_ms": 359000,                                              // opcional
  "enqueue": true,                    // também adiciona à fila
  "singer": "Ana",                    // opcional, usado se enqueue=true
  "confirm_long": false,
  "lyrics_mode": "online" }           // v0.2, opcional: sobrescreve o setting `lyrics_mode` só para este job
```
Modos (CR-001 — **o texto vem sempre da letra oficial; o Whisper só dá os tempos**; `transcribe` = alias de `aligned`):
- busca LRCLIB em cadeia (para no 1º resultado com tempos e duração a ±3 s): `/api/get` (título limpo + artista — do
  título ou do canal — + duração) e `/api/search` por campos; depois `q=<artista> <faixa>`; depois `q=<título do vídeo
  sem ruído>`. Registros só com texto (`plainLyrics`) viram candidatas `online_plain`.
- `online`: melhor LRCLIB **com tempos** e duração aceitável, como está; sem ela, igual a `aligned`.
- `aligned`: Whisper sobre o vocal + alinhamento do texto oficial, tentando (no máx. 3) LRCLIB com tempos e duração
  aceitável → `online_plain` com duração aceitável → mesma faixa com outra duração; a 1ª com cobertura ≥ 30 % gera a
  `aligned` selecionada. Se nenhuma casar mas houver LRCLIB com tempos aceitável → ela, como está. Só sem letra oficial
  nenhuma → texto do Whisper (`whisper`, "Extraída da voz (sem letra oficial)", `official_text: false`) + toast de aviso.
  A candidata `whisper` **não** é mais gravada quando há letra oficial.
A transcrição fica em cache em `data/songs/<id>/transcript.json` (reaproveitada por `/lyrics/align` e `/detect-offset`).
Respostas:
- `201 { "job": Job, "song": Song, "queue_item": QueueItem | null }` — novo job.
- `200 { "job": Job, "song": Song, "queue_item": … }` — já havia job ativo para a música (não duplica).
- `200 { "job": null, "song": Song, "queue_item": … }` — música já pronta (não reprocessa).
- `409 long_video_confirmation_required` (`details.duration_ms`), `400 invalid_request`.
Música com `status=error` é reprocessada (novo job).

**Convidado (v0.3)** — corpo efetivo: `{ "youtube_id": "…", "singer": "Bia" }` (demais campos ignorados; ver §0.1 ²).
Ordem das checagens: metadados (cache da busca → `probe`; sem rede `502 network_unavailable`; live → `400`) →
duração (`409 long_video_not_allowed`) → cria e enfileira na mesma transação (sem cotas: DECISION 0016).
Música já pronta → só enfileira (`job: null`); job ativo para a mesma música → só enfileira. Respostas iguais às do host (`queue_item.added_by = "g_<n>"`).
```json
409 { "error": { "code": "long_video_not_allowed", "message": "Vídeos com mais de 15 minutos não podem ser pedidos.",
                 "details": { "duration_ms": 1260000, "max_ms": 900000 } } }
```

### GET `/api/jobs?status=active|recent|all` `[host]`
`active` (padrão) = queued + em execução; `recent` = ativos + terminados nas últimas 24 h.
```json
200 { "jobs": [ Job, … ] }        // ordem: em execução, fila, terminados (mais recentes primeiro)
```

### GET `/api/jobs/{job_id}` → `200 Job` | `404 job_not_found` `[guest-safe]³`
### POST `/api/jobs/{job_id}/retry` → `200 Job` (volta a `queued`) | `409 job_not_retryable` `[host]`
### DELETE `/api/jobs/{job_id}` → `204` (cancela ativo ou remove terminado da lista) | `404` `[host]`

## 6. Biblioteca

### Objeto `Song` (resumo — usado em listas, fila, eventos)
```json
{
  "id": "fJ9rUzIMcZQ",
  "youtube_id": "fJ9rUzIMcZQ",
  "title": "Bohemian Rhapsody",       // limpo (sem "(Official Video)")
  "artist": "Queen",
  "original_title": "Queen – Bohemian Rhapsody (Official Video Remastered)",
  "channel": "Queen Official",
  "duration_ms": 359000,
  "status": "ready",                  // queued | processing | ready | error
  "job_id": null,                     // job ativo, se houver
  "thumbnail": "/media/songs/fJ9rUzIMcZQ/thumb.jpg",   // antes de pronta: "/api/thumbs/<id>"
  "lyrics": { "source": "lrclib", "timing": "line", "candidate_id": "lrclib:28374", "candidates": 3 },
  "offset_ms": 0,
  "media": { "instrumental": "/media/songs/fJ9rUzIMcZQ/instrumental.opus?v=1",
             "vocals": "/media/songs/fJ9rUzIMcZQ/vocals.opus?v=1", "version": 1 },   // null se não pronta
  "size_bytes": 11534336,
  "added_by": "host",                 // ator que disparou o processamento (definido pelo servidor)
  "can_remove": true,                 // added_by == ator da requisição (§0.2)
  "created_at": "2026-10-03T23:51:30Z"
}
```
`lyrics.source`: `lrclib` | `whisper` | `manual` | `null` (sem letra — Palco mostra "Letra indisponível" e toca só o áudio).

### GET `/api/songs?q=&status=ready|all&sort=recent|title|artist` `[guest-safe]`
Padrão `status=ready`, `sort=recent`. `q` filtra título/artista (case/acento-insensível).
```json
200 { "songs": [ Song, … ], "total": 12 }
```

### GET `/api/songs/{song_id}` → `200 Song` | `404` `[guest-safe]`
### PATCH `/api/songs/{song_id}` `[host]`
```json
{ "title": "Bohemian Rhapsody", "artist": "Queen", "offset_ms": -150 }     // qualquer subconjunto
```
→ `200 Song`. `offset_ms` ∈ [-10000, 10000]. Eventos `song.updated` (+ `player.state` se for a música atual).

### DELETE `/api/songs/{song_id}` → `204` | `403 not_owner` `[host]`
Remove arquivos, letras e itens **pendentes** da fila com essa música; cancela job ativo. `409 song_in_use` se
estiver tocando (`status` playing/paused/loading com esse `song_id`). Evento `library.updated {action:"removed"}`
+ `queue.updated`.
A cascata da FK apaga os itens `done`/`skipped` dessa música do histórico e os itens pendentes **do próprio ator**.
v0.3 (ADR-0003 §6): se houver itens **pendentes de outros atores** (convidados) → `409 song_queued_by_others`
(`details.count`) — o host não remove pedidos de convidados nem indiretamente (DECISION 0012).

## 7. Letras

Todas as rotas desta seção são `[host]` — inclusive as novas da v0.2 (`/align`, `/manual-timing`, `/detect-offset`). O campo `lyrics_mode` de `POST /api/jobs` (`[guest-safe]`) é aceito de qualquer ator (só escolhe como obter a letra da música que ele mesmo prepara).

### GET `/api/songs/{song_id}/lyrics`
→ `200` documento **Lyrics** ([lyrics-format.md](lyrics-format.md)) da candidata selecionada. `404 song_not_found`.
Sem letra: `200 { "version": 1, "song_id": "...", "source": null, "timing": "none", "lines": [] , ...}`.

### GET `/api/songs/{song_id}/lyrics/candidates`
```json
200
{
  "selected_id": "lrclib:28374",
  "candidates": [
    { "id": "aligned:3", "source": "aligned", "label": "Online · sincronizada pela voz", "timing": "word",
      "derived_from": "lrclib:28374",                       // v0.2: candidata de origem (texto)
      "track_name": "Bohemian Rhapsody", "artist_name": "Queen", "album_name": "A Night at the Opera",
      "model": "large-v3-turbo", "language": "en",
      "align": { "coverage": 0.96, "anchored_lines": 52, "lines": 54, "offset_ms": 1210 },
      "duration_ms": 358000, "duration_diff_ms": -1000, "line_count": 54, "preview": ["…"], "created_at": "…" },
    { "id": "lrclib:28374", "source": "lrclib", "label": "Online", "derived_from": null, "timing": "line",
      "track_name": "Bohemian Rhapsody", "artist_name": "Queen", "album_name": "A Night at the Opera",
      "duration_ms": 358000, "duration_diff_ms": -1000, "line_count": 54,
      "preview": ["Is this the real life?", "Is this just fantasy?", "Caught in a landslide"],
      "created_at": "2026-10-03T23:51:20Z" },
    { "id": "whisper:2", "source": "whisper", "label": "Extraída da voz", "derived_from": null, "timing": "word",
      "model": "large-v3-turbo", "language": "en",
      "duration_ms": null, "duration_diff_ms": null, "line_count": 49, "preview": ["…"], "created_at": "…" },
    { "id": "manual:4", "source": "manual", "label": "Manual", "derived_from": "aligned:3", "edited": true,
      "track_name": "…", "artist_name": "…", "timing": "word", "duration_ms": 358000, "duration_diff_ms": -1000,
      "line_count": 54, "preview": ["…"], "created_at": "…" }
  ]
}
```
Ordem: selecionada primeiro, depois por `|duration_diff_ms|`. v0.2: todas têm `label` (pt-BR, AC-08.4) e
`derived_from` (string ou null); `manual` tem `edited` (true = criada pelo editor de sincronia, false = LRC colado).
CR-001: todas têm `official_text` (false só em `whisper` → a UI sugere colar a letra) e `selectable` (false em
candidata só texto, `timing: "none"`). Rótulos: `aligned` "Online · sincronizada pela voz", `lrclib` "Online",
`online_plain` "Online · só texto" (mesmos campos de `lrclib`), `whisper` "Extraída da voz (sem letra oficial)",
`manual` "Manual" ou "Colada · só texto" (colada sem tempos).

### PUT `/api/songs/{song_id}/lyrics/selected`
```json
{ "candidate_id": "whisper:2" }
```
→ `200` Lyrics. `404 candidate_not_found`, `409 candidate_not_selectable` (só texto). Eventos `lyrics.updated`, `song.updated`.

### POST `/api/songs/{song_id}/lyrics/manual` — colar LRC
```json
{ "lrc": "[00:12.34]Is this the real life?\n[00:15.80]Is this just fantasy?\n…" }
```
Aceita LRC simples e Enhanced LRC (`<mm:ss.xx>` por palavra); tags `[ar:]`, `[ti:]`, `[offset:]` respeitadas.
→ `201` Lyrics (nova candidata `manual:<n>`, já selecionada). `422 invalid_lrc` (só tags/marcadores, sem texto).
**CR-001 — texto puro** (nenhuma tag de tempo; tags `[ar:]`/`[ti:]` ignoradas, linhas vazias descartadas) →
`202 { "candidate": <resumo da candidata, timing "none", label "Colada · só texto", selectable false>, "job": Job | null }`:
a candidata só texto é gravada (não selecionada) e, se a música está pronta, um job `align` com ela é enfileirado
(`job: null` se não estiver pronta — use `/align` depois). Ao terminar, a `aligned` derivada é selecionada.

### POST `/api/songs/{song_id}/lyrics/search` — rebuscar no LRCLIB com termos editados
```json
{ "track_name": "Bohemian Rhapsody", "artist_name": "Queen" }      // opcionais; padrão = título/artista atuais
```
→ `200 { "added": 2, "candidates": [ …mesmo formato de /candidates… ] }` (não troca a seleção).
`502 network_unavailable`.

### POST `/api/songs/{song_id}/lyrics/transcribe` — regerar com Whisper
```json
{ "language": "pt" }                 // opcional; null = detecção automática
```
→ `202 { "job": Job }` (kind `transcribe`). Ao terminar, a nova candidata `whisper:<n>` é **selecionada** e emite
`lyrics.updated`. `409 song_not_ready`. Disponível mesmo com letra online selecionada (AC-08.5); atualiza o cache
`transcript.json`. **CR-001:** mantida por compatibilidade (gera texto do Whisper); a UI usa `/align`
("Buscar letra oficial e sincronizar"). Se já houver um job `transcribe` ativo para a música, devolve o mesmo (`202`).

### POST `/api/songs/{song_id}/lyrics/align` — "Sincronizar pela voz" (v0.2, AC-08.5)
```json
{ "candidate_id": "lrclib:28374",    // opcional: candidata cujo TEXTO será usado (lrclib | manual | aligned)
  "language": "en" }                 // opcional: só usado se precisar transcrever
```
Sem `candidate_id`: a selecionada se tiver texto oficial (`lrclib`/`online_plain`/`manual`/`aligned`); senão a
`lrclib` (depois `online_plain`) de menor `|duration_diff_ms|`; senão a colada mais recente. Uma `aligned` é trocada
pela sua origem (`derived_from`). **CR-001 — nenhuma letra oficial guardada:** o job (`options.search`) busca no
LRCLIB antes (mesma cadeia do processamento; as encontradas ficam listadas) e alinha a melhor; se nada servir (ou sem
rede), a nova candidata selecionada é o texto do Whisper (`whisper`, `official_text: false`) — escolha documentada no
lugar de `409`, para a música não ficar sem letra; a UI sugere colar a letra. → `202 { "job": Job }` (kind `align`; reaproveita o job
ativo do mesmo tipo). Usa a transcrição em cache (`stage` vai direto a `lyrics`) ou transcreve o vocal (etapas
`model_download?` → `lyrics`). Ao terminar: nova candidata `aligned:<n>` (`derived_from` = origem) **selecionada**,
`lyrics.updated` (`source: "aligned"`), `song.updated`; as anteriores continuam listadas.
Erros: `404 song_not_found | candidate_not_found`, `409 song_not_ready`, `409 lyrics_text_required` (só quando o
`candidate_id` pedido é `whisper`); job termina em `error` com `align_failed` se nenhuma linha do texto pedido casar com
a voz, ou `transcription_failed` se, sem letra oficial, nem a voz for reconhecida.

### PUT `/api/songs/{song_id}/lyrics/manual-timing` — salvar o editor de sincronia (v0.2, AC-09.5)
```json
{ "candidate_id": "aligned:3",       // opcional: candidata editada (padrão: a selecionada); base para end_ms/words
  "lines": [                         // 1..2000 linhas, na ordem
    { "text": "Is this the real life?", "start_ms": 12340 },
    { "text": "Is this just fantasy?", "start_ms": 15800, "end_ms": 18900,
      "words": [ { "text": "Is", "start_ms": 15800, "end_ms": 16050 }, … ] }   // opcional
  ] }
```
Validação → `422 invalid_timing` com `details: { "line": <índice 0-based>, "reason": … }`:
`not_sorted` (start_ms ≤ o da anterior), `after_end` (start_ms ≥ duração da música), `end_before_start`,
`empty_text`, `words_mismatch` (palavras unidas por espaço ≠ `text`), `words_not_sorted`.
Limites do payload (`400 invalid_request`): `start_ms`/`end_ms` em 0..86 400 000, `text` ≤ 500 caracteres, ≤ 200
palavras por linha, 1..2000 linhas.
Completa o que faltar: `end_ms` ausente = duração da linha de mesmo texto/posição na base (limitada pela próxima
linha) ou a estimativa de lyrics-format §2.5; `words` ausentes = palavras da base reescaladas para o novo
intervalo, ou divisão proporcional por caracteres. O resultado segue as invariantes de lyrics-format §2.
→ `201` Lyrics da nova candidata `manual:<n>` (`derived_from` = base, `edited: true`), **selecionada**; a base
continua listada. Se a base já é uma `manual` criada pelo editor → atualizada **no lugar** → `200` Lyrics.
Eventos `lyrics.updated` (`source: "manual"`), `song.updated`, `library.updated`. `404 song_not_found | candidate_not_found`.

### POST `/api/songs/{song_id}/lyrics/detect-offset` — "Detectar automaticamente" (v0.2, AC-09.4)
```json
{ "candidate_id": "lrclib:28374" }   // opcional (padrão: a selecionada)
```
Com transcrição em cache → `200`:
```json
{ "song_id": "fJ9rUzIMcZQ", "candidate_id": "lrclib:28374", "current_offset_ms": 0,
  "suggested_offset_ms": 1200,       // convenção de lyrics-format §4: positivo = letra mais tarde
  "confidence": 0.82,                // 0..1 (cobertura de linhas casadas × dispersão)
  "anchored_lines": 49, "lines": 55, "spread_ms": 180, "drift_ms_per_min": 120.0 }
```
Sem cache → `202 { "job": Job }` (kind `analyze`, transcreve e grava o cache); o resultado chega pelo evento
`lyrics.offset_detected` (mesmo corpo do 200). Não altera o offset: o cliente aplica com
`PATCH /api/songs/{id}` (`offset_ms`) se o usuário aceitar. `confidence < 0.4` → sugerir com cautela (ou deriva
alta: preferir "Sincronizar pela voz"). Erros: `404 song_not_found | candidate_not_found`, `409 no_lyrics`,
`409 song_not_ready`.

## 8. Fila

### Objeto `QueueItem`
```json
{
  "id": "q_7",
  "song_id": "fJ9rUzIMcZQ",
  "singer": "Ana",                   // null se não informado
  "status": "pending",               // pending | playing | done | skipped
  "ready": true,                     // song.status == ready
  "song": Song,                      // resumo (inclui status/job_id para itens processando)
  "job": { "stage": "separating", "progress": 0.57 },   // só se a música ainda está processando; senão null
  "added_by": "host",               // quem enfileirou: "host" | "g_<n>" (id do convidado)
  "added_by_name": null,             // v0.3: nome do convidado no momento do pedido; null quando added_by = "host"
  "can_remove": true,                // added_by == ator da requisição (§0.2)
  "added_at": "2026-10-03T23:52:00Z"
}
```
`added_by` e `added_by_name` são definidos **pelo servidor** a partir do principal resolvido em `authorize` — nunca
aceitos no corpo da requisição. UI: "pedido por <added_by_name>" quando não-nulo (AC-16.1); `singer` pode diferir.

### GET `/api/queue` `[guest-safe]`
```json
200 { "revision": 18, "current": QueueItem | null, "items": [ QueueItem, … ], "history": [ QueueItem, … ] }
```
`items` = pendentes na ordem de reprodução; `history` = últimos 20 `done`/`skipped` (mais recente primeiro).

### POST `/api/queue` `[guest-safe]³`
```json
{ "song_id": "fJ9rUzIMcZQ", "singer": "Ana", "position": "end" }   // position: "end" (padrão) | "next" | índice inteiro
```
→ `201 QueueItem`. Música em processamento é aceita (`ready:false`); músicas com `status=error` → `409 song_not_ready`.
Convidado: `{ "song_id": "…", "singer": "Bia" }` (`position` omitido ou `"end"`), sem cota (§0.1 ⁴).
Se o player está `idle` e o item está pronto e `autoplay=true`, o servidor inicia (`loading`).

### PATCH `/api/queue/{item_id}` → `{ "singer": "Bruno" }` → `200 QueueItem` `[host]`
### DELETE `/api/queue/{item_id}` → `204` (remover o item `playing` equivale a `next`) | `403 not_owner` `[guest-safe]⁵`
v0.3: convidado remove só os próprios itens `pending` (`409 queue_item_playing` para o que está tocando).
### DELETE `/api/queue` → `204` limpa todos os pendentes (não interrompe o atual) `[host]`

### POST `/api/queue/reorder` — arrastar e soltar `[host]`
```json
{ "revision": 18, "item_ids": ["q_9", "q_7", "q_8"] }     // ordem completa dos pendentes
```
→ `200` (mesmo corpo de GET `/api/queue`). `409 stale_queue` se `revision` ≠ atual (UI recarrega e reaplica);
`400` se a lista não bate com os pendentes. `revision` é opcional (omitida = sem checagem).

### POST `/api/queue/{item_id}/move` — teclado/botões `[host]`
```json
{ "to_index": 0 }                  // ou { "direction": "up" | "down" | "top" | "bottom" }
```
→ `200` (corpo de GET `/api/queue`).

Toda mudança emite `queue.updated` com o corpo completo de GET `/api/queue`.

## 9. Player

### GET `/api/player` → `200 PlayerState` `[guest-safe]`
```json
{
  "revision": 42,
  "status": "playing",          // idle | waiting_gesture | loading | playing | paused | up_next | ended
  "queue_item_id": "q_7",
  "song_id": "fJ9rUzIMcZQ",
  "song": Song,                 // resumo da música atual (null em idle)
  "singer": "Ana",
  "position_ms": 63250,
  "position_at": 1791167000123, // epoch ms do servidor (Date.now compatível)
  "duration_ms": 359000,
  "volume": 0.8,
  "guide_vocal": 0.0,
  "offset_ms": 0,
  "autoplay": true,
  "up_next": null,              // { "queue_item_id": "q_8", "song": Song, "singer": "Bruno", "added_by_name": "Bia" | null (v0.3), "starts_at": 1791167008000 | null }
  "stage": { "connected": 1, "audio_owner": "c_ab12", "unlocked": true }
}
```
- `waiting_gesture`: há música para tocar mas nenhum Palco reivindicou o áudio (Palco mostra "Pressione Enter").
- `up_next.starts_at`: null quando `autoplay=false` (espera `play`).
- `ended`: fila acabou após a última música (Palco mostra tela de espera; equivale a `idle` com histórico).

### POST `/api/player/command` `[host]`
```json
{ "action": "seek", "value": 90000, "client_id": "c_ab12" }
```
| `action` | `value` | Efeito |
|----------|---------|--------|
| `play` | — | Toca/retoma; em `idle` inicia o próximo item pronto; em `up_next` começa já |
| `pause` | — | Pausa |
| `toggle` | — | play/pause |
| `next` | — | Pula para o próximo item pronto (atual → `skipped`) |
| `prev` | — | Reinicia se posição > 3 s; senão volta ao último item do histórico |
| `restart` | — | Volta a 0 |
| `seek` | int ms (absoluto) | Clamp em [0, duration] |
| `seek_relative` | int ms (±) | Ex.: ±5000 (teclas ←/→) |
| `volume` | float 0..1 | Persistido em settings |
| `volume_relative` | float (±) | Ex.: ±0.05 (↑/↓) |
| `guide_vocal` | float 0..1 | Persistido em settings |
| `guide_vocal_relative` | float (±) | Ex.: ±0.1 |
| `offset` | int ms (absoluto) | Offset da letra da música atual, persistido em `songs.offset_ms` |
| `offset_relative` | int ms (±) | `[`/`]` = ∓50 |
| `autoplay` | bool | Liga/desliga auto-avanço |
| `start` | `queue_item_id` (string) | Toca esse item agora (o atual vira `skipped`) |

→ `200 PlayerState` (já atualizado) | `400 invalid_request` | `409 stage_not_connected` (play/start/toggle→play
sem audio owner; o servidor deixa `status=waiting_gesture`) | `409 song_not_ready` (start de item não pronto).
Todas as mudanças emitem `player.state`.

### POST `/api/player/report` — somente Palco (audio owner) `[host]`
```json
{ "client_id": "c_ab12", "queue_item_id": "q_7", "song_id": "fJ9rUzIMcZQ",
  "position_ms": 63250, "playing": true,
  "event": "tick" }           // tick | loaded | started | paused | seeked | ended | error
```
`error` aceita `"error": { "code": "decode_failed", "message": "…" }` → servidor marca o item `skipped` e avança.
→ `204` sempre (reports de não-owner ou de item antigo são ignorados). Frequência: `tick` a cada 500 ms enquanto toca
+ imediatamente em cada evento. Servidor republica como `player.position` (≤ 4 Hz) e transições como `player.state`.

### POST `/api/stage/claim` `[host]`
```json
{ "client_id": "c_ab12" }
```
→ `200 PlayerState` com `stage.audio_owner = "c_ab12"`, `stage.unlocked = true`. Chamar após o gesto do usuário
(Enter/clique) que destrava o `AudioContext`. Se `status=waiting_gesture`, o servidor passa para `loading`.

## 10. Eventos em tempo real (SSE)

### GET `/api/events?client_id={id}&role={control|stage|guest}` `[guest-safe]⁶`
`Content-Type: text/event-stream`. Cada evento:
```
id: 1043
event: player.state
data: {"revision":43,"status":"paused", …}

```
- Ao conectar, o servidor envia **`hello`** com o snapshot completo; depois, eventos incrementais.
- Keep-alive: comentário `: ping` a cada 15 s. Reconexão: o `EventSource` reconecta sozinho (`retry: 1000`);
  a cada reconexão chega um novo `hello` (não há replay por `Last-Event-ID`).
- Conexão `role=stage` conta em `stage.connected`.
- v0.3: `role=guest` é exclusivo de convidados (e convidados só usam `role=guest`). Recebem apenas os eventos marcados
  com ✓G abaixo; `job.updated`/`job.removed` só dos jobs com `requested_by` = o próprio convidado.

| `event` | `data` | Quando |
|---------|--------|--------|
| `hello` | host: `{ "server_time": 1791167000123, "client_id": "c_ab12", "player": PlayerState, "queue": <GET /api/queue>, "jobs": [Job ativos], "settings": Settings, "system": { "device": "cuda", "gpu_warning": null, "model_download": null }, "guest_state": GuestState }` · ✓G convidado: `{ "server_time", "client_id", "guest": GuestMe, "player", "queue", "jobs": [Jobs ativos dele] }` (sem `settings`/`system`/`guest_state`) | ao conectar |
| `player.state` | `PlayerState` ✓G | qualquer mudança de intenção/status/fila atual/owner |
| `player.position` | `{ "queue_item_id": "q_7", "position_ms": 63250, "position_at": 1791167000123, "playing": true }` | reports do Palco, ≤ 4 Hz |
| `queue.updated` | corpo de GET `/api/queue` ✓G | qualquer mudança na fila (incl. `ready` mudando) |
| `job.updated` | `Job` ✓G (só os dele) | criação, troca de etapa, progresso (≤ 4 Hz por job), fim, erro |
| `job.removed` | `{ "id": "j_31" }` ✓G (só os dele) | job removido da lista |
| `library.updated` | `{ "action": "added" \| "updated" \| "removed", "song_id": "…", "song": Song \| null }` ✓G | música pronta, editada, excluída |
| `song.updated` | `Song` ✓G | offset, título, seleção de letra |
| `lyrics.updated` | `{ "song_id": "…", "candidate_id": "whisper:2", "source": "whisper" }` | letra selecionada mudou → Palco refaz GET da letra se for a música atual/próxima |
| `lyrics.offset_detected` | corpo do `200` de `/lyrics/detect-offset` | v0.2: fim de um job `analyze` |
| `settings.updated` | `Settings` | |
| `system.updated` | `{ "device": "cuda", "gpu_warning": null, "model_download": { "model_id": "htdemucs_ft", "progress": 0.4 } \| null }` | download de modelo, mudança de device |
| `toast` | `{ "level": "info" \| "warning" \| "error", "message": "Letra encontrada no LRCLIB" }` | avisos opcionais do servidor para o Controle |
| `guest.state` | `GuestState` (§12.1) — **só host** (`control`/`stage`; contém a URL com o token) | v0.3: ligar/desligar, rotação (manual ou automática), encerrar, sessão criada/expirada, conexão de convidado aberta/fechada (≤ 1/s) |
| `guest.ended` | `{ "reason": "ended" \| "disabled" \| "expired" }` ✓G — **só convidado** | v0.3: sessão encerrada ("Encerrar convidados", modo desligado, sessão expirou); o servidor fecha o stream logo depois |

## 11. Mapa de uso por tela (guia para o frontend)

**Controle** (`/`): `EventSource(/api/events?role=control)` → estado inicial do `hello`.
Busca `GET /api/search` · Preparar `POST /api/jobs` · Processamento: eventos `job.*`, `retry`/`DELETE` ·
Biblioteca `GET /api/songs`, `DELETE`, `PATCH` (offset) · Letras: `/lyrics/candidates`, `/selected`, `/manual`,
`/search`, `/transcribe`, `/align`, `/manual-timing`, `/detect-offset` (v0.2) · Fila `/api/queue*` · Mini-player `POST /api/player/command` + `player.position` ·
"Abrir Palco" = `window.open('/stage', 'karaoke-stage')`.

**Palco** (`/stage`): `EventSource(/api/events?role=stage)` → tela "Pressione Enter" → `POST /api/stage/claim` →
para cada `player.state` com `song_id`/`status` novos: `GET /api/songs/{id}/lyrics` + baixar `song.media.*`,
decodificar, `report {event:"loaded"}`; aplicar play/pause/seek/volume/guide do estado; `report tick` 2 Hz;
teclas → `POST /api/player/command`.
v0.3: `hello.guest_state` + evento `guest.state` → com `enabled` e `player.status ∈ {idle, ended, up_next,
waiting_gesture}`, mostrar `<img src=guest_state.qr>` e `display_url`; "Pedido por" = `up_next.added_by_name`.

**Controle — painel Modo convidado** (v0.3): `GET /api/guest/state` (ou `hello.guest_state`) + `guest.state` ·
toggle → `POST /api/guest/enable {address}` / `disable` · "Novo QR" `rotate` · "Encerrar convidados" `end-sessions` ·
copiar `GuestState.url` · seletor com `addresses[]` · dica `firewall_hint`. Fila: "pedido por `added_by_name`".

**Convidado** (`/guest`, v0.3): ver §12.3.

Exemplo `curl`:
```sh
curl -s 'http://127.0.0.1:8765/api/search?q=bohemian%20rhapsody' | jq '.results[0]'
curl -s -XPOST localhost:8765/api/jobs -H 'content-type: application/json' \
     -d '{"youtube_id":"fJ9rUzIMcZQ","enqueue":true,"singer":"Ana"}'
curl -N 'http://127.0.0.1:8765/api/events?client_id=c_test&role=control'
curl -s -XPOST localhost:8765/api/player/command -H 'content-type: application/json' -d '{"action":"toggle"}'
```

## 12. Modo convidado (v0.3) — [ADR-0003](../adr/0003-guest-mode.md)

O anfitrião liga o modo no Controle; o servidor passa a escutar **também** em `<ip-lan>:<porta>` (mesmo app). O
Palco mostra o QR com `http://<ip-lan>:<porta>/guest#t=<token>`. Nada é persistido: ao reiniciar, o modo volta a
desligado (AC-11.5). Rotas antigas reservadas na v0.2 (`/api/guest/status`, `/api/guest/sessions`) **não** existem.

### 12.1 Objetos

**`GuestState`** (só host):
```json
{
  "enabled": true,
  "url": "http://192.168.1.16:8765/guest#t=Vx3kQ9-LmZp0aB7cD2eF4g",   // null se desligado. Contém o token: só host
  "display_url": "192.168.1.16:8765/guest",     // texto curto sob o QR (sem token); null se desligado
  "qr": "/api/guest/qr?v=3",                    // null se desligado; muda a cada rotação
  "invite_version": 3,                          // 0 se desligado
  "invite_expires_at": "2026-10-05T14:10:00Z",  // rotação automática ao expirar (12 h)
  "sessions": 4,                                // sessões válidas
  "connected": 3,                               // convidados distintos com SSE aberto
  "address": "192.168.1.16",                    // IP em uso (null se desligado)
  "port": 8765,
  "addresses": [                                // candidatos (sempre preenchido, mesmo desligado)
    { "address": "192.168.1.16", "interface": "enp6s0", "kind": "ethernet", "recommended": true },
    { "address": "192.168.1.4",  "interface": "wlan0",  "kind": "wifi",     "recommended": false }
  ],                                            // kind: ethernet | wifi | vpn | other (virtual/docker nunca aparece)
  "firewall_hint": { "tool": "ufw", "command": "sudo ufw allow 8765/tcp" },   // ou tool "firewall-cmd"; null se nenhum
  "error": null                                 // { "code": "lan_bind_failed", "message": "…" } do último enable, se falhou
}
```

**`GuestMe`** (convidado):
```json
{ "id": "g_7", "name": "Bia", "expires_at": "2026-10-05T14:20:00Z",
  "limits": { "max_duration_ms": 900000, "search_per_min": 30 } }
```
DECISION 0016: `limits.pending_items` e `limits.active_jobs` **saíram** (não há cotas por convidado). O cliente não
deve bloquear pedidos por contagem de pendentes/preparos.

### 12.2 Rotas do anfitrião `[host]`

| Método | Rota | Corpo | Resposta |
|--------|------|-------|----------|
| GET | `/api/guest/state` | — | `200 GuestState` |
| POST | `/api/guest/enable` | `{ "address": "192.168.1.16" }` (opcional; padrão = `recommended`) | `200 GuestState` (já ligado com outro `address` → troca de IP: reabre o listener; sessões caem porque o cookie é por host). `400 invalid_request` (endereço fora de `addresses`), `409 lan_unavailable`, `409 lan_bind_failed` |
| POST | `/api/guest/disable` | — | `200 GuestState` (idempotente). Envia `guest.ended {reason:"disabled"}`, apaga sessões e convite, fecha o listener |
| POST | `/api/guest/rotate` | — | `200 GuestState` com `invite_version` + 1 ("Novo QR"; sessões continuam). `409 guest_mode_off` |
| POST | `/api/guest/end-sessions` | — | `200 GuestState` com `sessions: 0` ("Encerrar convidados": invalida todas as sessões, envia `guest.ended {reason:"ended"}` **e rotaciona** o convite; modo continua ligado). `409 guest_mode_off` |
| GET | `/api/guest/qr?v=<n>` | — | `200 image/svg+xml` (preto/branco, `viewBox`, borda 4 módulos), `Cache-Control: no-store`. `409 guest_mode_off` |

Toda mudança emite `guest.state`. Exemplo:
```sh
curl -s -XPOST localhost:8765/api/guest/enable -H 'content-type: application/json' -d '{"address":"192.168.1.16"}' | jq .url
```
```json
409 { "error": { "code": "lan_bind_failed", "message": "Não foi possível abrir a porta 8765 em 192.168.1.16.",
                 "details": { "address": "192.168.1.16", "port": 8765, "reason": "EADDRINUSE" } } }
```

### 12.3 Rotas do convidado

#### POST `/api/guest/session` `[public]` — entrar
```json
{ "token": "Vx3kQ9-LmZp0aB7cD2eF4g", "name": "Bia" }
```
→ `201 { "guest": GuestMe }` + `Set-Cookie: karaoke_guest=<32 bytes urlsafe>; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200`.
Erros: `400 invalid_request` (`details.fields.name`: vazio/> 40 após saneamento; `token` ausente),
`401 guest_invite_invalid` ("Este QR não vale mais — escaneie o QR da TV de novo"), `429 rate_limited`
(`reason: session` — balde de taxa por IP, §0.1 — ou `too_many_guests`), `403 forbidden` (`guest_mode_off`; chamado do loopback → host não cria
sessão de convidado). Se já houver sessão válida no cookie, ela é **substituída** (nova id; os streams SSE da
antiga são fechados). Não há limite de sessões por IP (DECISION 0016: vários convidados atrás do mesmo NAT/repetidor
entram normalmente). `too_many_guests` (teto de 50 sessões **ativas**) conta só as que tiveram requisição nos últimos
15 min ou têm SSE aberto: as inativas continuam válidas, mas não ocupam vaga.

#### GET `/api/guest/session` `[public]` — "já estou dentro?" (recarregar a página)
→ `200 { "guest": GuestMe }` · `401 guest_session_required` (sem cookie / expirada / encerrada).

#### DELETE `/api/guest/session` `[guest-safe]` — sair
→ `204` + `Set-Cookie: karaoke_guest=; Max-Age=0; Path=/`. Os itens dele na fila permanecem.

#### Demais rotas usadas pela página `/guest`
| Uso | Rota (já existente) | Notas para convidado |
|-----|---------------------|----------------------|
| Biblioteca ("Pronta") | `GET /api/songs?q=&status=ready` | balde `read` |
| YouTube | `GET /api/search?q=` | balde `search` (30/min, rajada 10) |
| Pedir música pronta | `POST /api/queue { song_id, singer }` | §0.1 ⁴ |
| Pedir música nova | `POST /api/jobs { youtube_id, singer }` | §0.1 ² |
| Progresso dos meus pedidos | SSE `job.updated` (só os meus) / `GET /api/jobs/{id}` | §0.1 ³ |
| Fila e tocando agora | SSE `hello`, `queue.updated`, `player.state` | posição: índice em `queue.items` |
| Remover meu pedido | `DELETE /api/queue/{id}` | §0.1 ⁵ |
| Capas | `/media/songs/{id}/thumb.jpg`, `/api/thumbs/{id}` | balde `read` |

Mapa de erros → UI: `401 guest_session_required` ou `guest.ended` → "Sessão encerrada" (com botão "Entrar de novo"
→ "escaneie o QR da TV"); `401 guest_invite_invalid` → "Este QR não vale mais"; `429` → "Calma! Tente em alguns
segundos" (usar `retry_after_s`); `409 long_video_not_allowed` →
mensagem do servidor (sem botão de confirmar). Falha de rede persistente (3 tentativas) após estar logado →
"Sessão encerrada — o anfitrião pode ter desligado o modo convidado".

Fluxo da página (`/guest#t=<token>`): ler `t` do fragmento → `history.replaceState(null, "", "/guest")` (AC-13.1) →
guardar o token em `sessionStorage` até criar a sessão → `GET /api/guest/session` (200 → app; 401 → tela de nome se
houver token, senão "Escaneie o QR da TV") → `POST /api/guest/session` → apagar o token do `sessionStorage` →
`EventSource('/api/events?role=guest&client_id=c_…')`. Nome sempre via `textContent`.

### 12.4 Matriz AC → mecanismo

| AC | Mecanismo |
|----|-----------|
| 11.1 | Sem listener na LAN com o modo desligado (conexão recusada); `authorize` passo 3 (`guest_mode_off`) |
| 11.2 | `POST /api/guest/enable` (sem reiniciar: 2º listener) → `GuestState.url`, `sessions`, `connected`; aviso é UI |
| 11.3 | `POST /api/guest/rotate` → `guest.state` → Palco troca `qr` (< 2 s); sessões mantidas |
| 11.4 | `end-sessions` → `401 guest_session_required` + `guest.ended`; `disable` → `guest.ended` + listener fechado |
| 11.5 | Estado só em memória; `GuestService` inicia desligado |
| 12.1–12.3 | `GuestState.enabled/qr/display_url` + regra de UI por `player.status` (ADR-0003 §7) |
| 12.4 | `GET /api/guest/qr` (segno, local) com `<ip-lan>` |
| 13.1–13.3 | `/guest` `[public]`, fragmento + `replaceState`, `POST`/`GET /api/guest/session`, `401 guest_invite_invalid` |
| 13.4 | DESIGN + página C; < 150 KB |
| 14.1 | `GET /api/songs` (Biblioteca) antes de `GET /api/search` (YouTube) — ordem na UI |
| 14.2 | `POST /api/queue` (fim, `singer`, `added_by`, `added_by_name`) → `queue.updated` |
| 14.3 | `POST /api/jobs` de convidado (`enqueue` forçado, modo de letra do host) + `job.updated` filtrado |
| 14.4 | `409 long_video_not_allowed` (duração apurada pelo servidor) + `video_too_long` no job |
| 14.5 | Sem cotas por convidado (DECISION 0016); `429 rate_limited` (baldes `search`/`write`, anti-loop) |
| 15.1 | SSE `role=guest` (`hello`, `queue.updated`, `player.state`), `can_remove`, `added_by == me.id` |
| 15.2 | `DELETE /api/queue/{id}` `guest-safe` + posse → `403 not_owner` |
| 15.3 | Escopos `host` (settings/system/jobs lista/áudio → 403) + filtro `visible_to` |
| 16.1 | `QueueItem.added_by_name`; o host remove qualquer item, inclusive de convidados (`can_remove=true`, DECISION 0006) |
| 16.2 | `PlayerState.up_next.added_by_name` |
