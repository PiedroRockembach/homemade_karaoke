# ADR-0001 — Stack do karaokê local

- Status: proposto (aguarda aprovação do usuário na fase SPEC)
- Data: 2026-10-03
- Autor: architect · Ciclo: `20261003-2339-karaoke`

## Contexto

**Requisitos que pesam** (SPEC §2–3):
- Busca e download do YouTube sem API key (US-01/02).
- Separação voz/instrumental por IA na GPU, ≤ 90 s para 4 min, VRAM ≤ 8 GB, fallback CPU (US-02).
- Letra sincronizada: LRCLIB → fallback Whisper com timestamps por palavra sobre a faixa de voz (US-03).
- Duas telas (Controle `/` e Palco `/stage`) sincronizadas em ≤ 300 ms; letra guiada pelo relógio do áudio com
  desvio ≤ 100 ms; voz guia 0–100 % em tempo real sem cortes (US-05/06/07).
- Persistência de biblioteca, fila, estado do player e jobs; retomada após reinício (US-02/04/05).
- 127.0.0.1, sem CDN, 1 comando para instalar e 1 para rodar, tema claro/escuro, AA.

**Ambiente detectado** (verificado em 2026-10-03):

| Item | Valor |
|------|-------|
| SO | Linux (CachyOS), shell fish, `make` e `gcc` presentes |
| Python | 3.14.7 do sistema, `venv` e `pip` ok, `pipx` presente; **sem uv** |
| JS runtimes | **nenhum** (sem node/bun/deno) |
| Outros | Go 1.27.1, ffmpeg n9.0.2 |
| GPU | RTX 5060 8 GB, compute capability **12.0 (Blackwell, sm_120)**, driver 615.71, CUDA UMD 13.4 |

**Verificação de wheels para Python 3.14 (cp314, linux x86_64) no PyPI / download.pytorch.org:**

| Pacote | Versão | cp314? | Observação |
|--------|--------|--------|------------|
| torch | 2.14.1 (PyPI) | sim | PyPI default = **CUDA 13.0** |
| torch | 2.13.0+cu129 (índice `whl/cu129`) | sim | CUDA 12.9 → suporta sm_120 e compartilha libs CUDA 12 com CTranslate2 |
| torch | 2.14.1+cu126 | sim | **não serve**: CUDA 12.6 não tem sm_120 |
| torch | 2.13.0+cpu / 2.14.1+cpu | sim | fallback sem GPU |
| demucs | 4.1.0 | py3-none-any | deps (einops, julius, lameenc, sphn, safetensors, huggingface-hub) todas com cp314/abi3 |
| faster-whisper | 1.2.1 | py3-none-any | |
| ctranslate2 | 4.8.2 | sim | wheels compilados com **CUDA 12.8**, cuDNN desligado, carga dinâmica de `libcublas.so.12` |
| audio-separator | 0.47.0 | py3-none-any | depende de `diffq` (**só sdist** → compilação C), onnx-weekly, librosa, onnx2torch… |
| openai-whisper | 20250625 | só sdist | depende de numba/triton (há cp314) |
| yt-dlp | 2026.8.19 | sim | YouTube **exige runtime JS** (EJS) → extra `yt-dlp[default,deno]` instala o binário Deno dentro do venv |
| fastapi 0.142 / uvicorn 0.54 / pydantic-core 2.49 / httpx 0.28 | — | sim | |
| ruff 0.16, pytest 9.1, playwright 1.63 | — | sim | |

**Conclusão do risco "Python 3.14 / Blackwell"**: *não é necessário* uv nem Python 3.12. O Python do sistema com `venv`
atende tudo. O conflito real é outro: torch do PyPI usa CUDA 13 e CTranslate2 (faster-whisper) precisa de CUDA 12.
Resolvido instalando `torch==2.13.0+cu129` do índice da PyTorch (traz `nvidia-cublas-cu12` 12.9, que o CTranslate2 usa).

## 1. Decisão principal — arquitetura

### Opções consideradas
1. **A — Python (FastAPI) + navegador, UI em HTML/JS vanilla sem build** — um processo servidor; IA em subprocesso Python.
2. **B — Servidor Go (binário) + worker Python para IA** — Go serve UI/API/estado; chama scripts Python para separar/transcrever.
3. **C — App desktop pywebview (Python) com duas janelas** — mesma lógica de A, janela nativa.
4. **D — 100 % no navegador** (ONNX Runtime Web/WebGPU para separação e Whisper) — sem backend.

### Matriz de decisão (1–5, peso entre parênteses)
| Critério | A | B | C | D |
|----------|---|---|---|---|
| Atende os Must (×3) | 5 | 5 | 4 | 1 |
| Simplicidade / poucas peças (×3) | 5 | 2 | 3 | 2 |
| Facilidade de instalar e rodar (×2) | 4 | 3 | 3 | 4 |
| Ecossistema p/ o domínio (×2) | 5 | 3 | 5 | 2 |
| Acesso a recursos do SO (×1) | 5 | 5 | 5 | 1 |
| Performance (×1) | 4 | 5 | 4 | 2 |
| **Total ponderado** | **57** | 43 | 46 | 24 |

- D falha nos Must: yt-dlp não roda no navegador (CORS/assinaturas), LRCLIB/YouTube exigiriam proxy, separação de alta
  qualidade em WebGPU com 8 GB é imatura e lenta.
- B duplica a linguagem sem ganho: o núcleo (yt-dlp, demucs, faster-whisper) é Python de qualquer forma.
- C não traz nada que o navegador não faça: o Palco precisa de tela cheia na TV (Fullscreen API resolve), e a
  "janela de controle" é só outra aba. Adiciona dependência GTK/Qt.

## 2. Sub-decisões

### 2.1 Separação de voz
| Critério (peso) | demucs 4.1 `htdemucs_ft` | audio-separator (BS/Mel-Roformer) | spleeter |
|---|---|---|---|
| Qualidade vocal/instrumental (×3) | 4 (SDR vocal ~9–10 dB) | 5 (SDR vocal ~11–12 dB) | 2 |
| Instalação limpa em 3.14 (×3) | 5 (tudo com wheel) | 2 (`diffq` só sdist, `onnx-weekly`, muitas deps) | 1 (TensorFlow, sem 3.14) |
| Velocidade GPU 4 min (×2) | 4 (~25–40 s; `htdemucs` ~10 s) | 3 (~30–60 s) | 5 |
| VRAM ≤ 8 GB (×1) | 5 | 4 | 5 |
| Manutenção (×1) | 3 (mantido, sem features novas) | 4 | 1 |
| **Total** | **43** | 35 | 25 |

**Decisão:** `demucs==4.1.0`, modelo padrão `htdemucs_ft` (configurável para `htdemucs` se precisar de velocidade).
Saída: `vocals` e `instrumental = drums + bass + other`. Roformer fica como upgrade futuro (novo ADR) caso a
qualidade não agrade — o contrato não muda.

### 2.2 Transcrição (fallback de letra)
| Critério (peso) | faster-whisper 1.2 | stable-ts (openai-whisper) | whisperX |
|---|---|---|---|
| Timestamps por palavra (×3) | 4 (`word_timestamps=True`) | 5 | 5 (wav2vec2 align) |
| Velocidade / VRAM (×2) | 5 (CTranslate2 fp16, turbo ~2 GB) | 3 | 4 |
| Instalação em 3.14 (×3) | 4 (precisa CUDA 12 → torch cu129) | 3 (openai-whisper só sdist) | 1 (pyannote, pins de torch) |
| VAD embutido (×1) | 5 (Silero) | 4 | 5 |
| **Total** | **39** | 34 | 31 |

**Decisão:** `faster-whisper==1.2.1`, modelo `large-v3-turbo` fp16 na GPU; na CPU `small` int8 (configurável).
Roda sobre a faixa de **voz separada** com `vad_filter=True`, `word_timestamps=True`.

### 2.3 UI
| Opção | Prós | Contras |
|---|---|---|
| **HTML + CSS + JS vanilla (ES modules), sem build** ✔ | Sem Node, zero dependência, servido direto | Mais código manual de DOM |
| SPA (Svelte/Vue) compilada | Componentização | Exige instalar Node + bundler; estado não é complexo o bastante |
| htmx/Alpine vendorizados | Pouco JS | Palco precisa de JS imperativo (Web Audio, rAF) de qualquer forma |

**Decisão:** vanilla ES modules, sem bundler. Nenhuma lib de terceiros no front (drag-and-drop nativo HTML5 +
botões/teclado). Fontes vendorizadas em `web/fonts/` (escolha do designer).

### 2.4 Tempo real
| Opção | Prós | Contras |
|---|---|---|
| **SSE (servidor→cliente) + REST (comandos)** ✔ | `EventSource` reconecta sozinho, sem dependência extra, comandos testáveis por `curl`, latência local < 20 ms | Unidirecional (comandos vão por POST — aceitável) |
| WebSocket | Bidirecional | Requer `websockets`/`wsproto`, reconexão manual, protocolo próprio de mensagens |

**Decisão:** SSE em `GET /api/events` + REST. O Palco reporta posição por `POST /api/player/report` (2 Hz).

### 2.5 Persistência
**SQLite** (`sqlite3` da stdlib, modo WAL) para músicas, letras, jobs, fila, estado do player e configurações.
Áudio e thumbnails como arquivos em `data/songs/<id>/`. Sem ORM; migrações versionadas com `PRAGMA user_version`.

### 2.6 Runtime Python
| Opção | Decisão |
|---|---|
| **Python 3.14 do sistema + `venv` + pip** ✔ | Todas as wheels necessárias existem (ver tabela). Zero pré-requisito novo. |
| uv + Python 3.12 gerenciado | Desnecessário hoje; fica como plano B documentado se alguma wheel quebrar. |

### 2.7 Áudio no navegador
Stems codificados em **Ogg Opus 160 kbps** (~5 MB por stem de 4 min; decodificado nativamente por Chrome/Firefox).
Reprodução via **Web Audio API com dois `AudioBuffer` iniciados no mesmo instante** (sincronia perfeita entre
instrumental e voz; relógio = `AudioContext.currentTime`). Detalhes em ARCHITECTURE.md §5.

## Decisão (resumo)

**Arquétipo B: servidor local Python 3.14 (FastAPI + uvicorn) em 127.0.0.1 servindo UI HTML/CSS/JS vanilla sem build;
IA (demucs `htdemucs_ft` + faster-whisper `large-v3-turbo`) em subprocesso sobre `torch 2.13.0+cu129`;
yt-dlp com Deno no venv; LRCLIB via httpx; SQLite; tempo real por SSE + REST; áudio via Web Audio.**

| Camada | Escolha | Versão fixada |
|--------|---------|---------------|
| Linguagem/runtime | CPython do sistema + venv em `.venv/` | 3.14.x |
| Servidor | FastAPI + uvicorn (sem extras) | `fastapi~=0.142`, `uvicorn~=0.54` |
| GPU stack | torch do índice `https://download.pytorch.org/whl/cu129` (CPU: `/whl/cpu`) | `torch==2.13.0` |
| Separação | demucs | `demucs==4.1.0` |
| Transcrição | faster-whisper (CTranslate2) | `faster-whisper~=1.2.1` |
| YouTube | yt-dlp + EJS + Deno (pip) | `yt-dlp[default,deno]>=2026.8.19` |
| HTTP cliente | httpx (LRCLIB, thumbnails) | `httpx~=0.28` |
| Áudio I/O | ffmpeg do sistema (download→wav, wav→opus), `soundfile` | `soundfile~=0.14` |
| Persistência | sqlite3 (stdlib) WAL | — |
| Front | HTML/CSS/JS vanilla (ES modules), Web Audio, EventSource | — |
| Testes | pytest (+ `httpx`/TestClient); e2e com Playwright Python (extra `e2e`) | `pytest~=9.1`, `playwright~=1.63` |
| Lint/format | ruff (Python); `deno lint` / `deno fmt` (JS/CSS/HTML — Deno já vem no venv) | `ruff~=0.16` |
| Comandos | `Makefile`: `install`, `start`, `dev`, `test`, `lint`, `fmt`, `update-ytdlp` | — |

### Plano concreto para GPU (Blackwell / CUDA)
1. `scripts/install.sh` cria `.venv` com `python3 -m venv`.
2. Se `nvidia-smi` existe: `pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cu129`;
   senão: `--index-url https://download.pytorch.org/whl/cpu`. Variável `KARAOKE_TORCH_INDEX` permite forçar.
3. `pip install -e .[dev]` com `constraints.txt` contendo `torch==2.13.0` (impede o pip de "atualizar" para 2.14.1/CUDA 13 do PyPI).
4. CTranslate2 encontra `libcublas.so.12` porque o worker **importa `torch` antes de `faster_whisper`** (as libs
   `nvidia-*-cu12` já ficam carregadas no processo). Salvaguarda: o worker também adiciona
   `site-packages/nvidia/*/lib` ao `LD_LIBRARY_PATH` do subprocesso.
5. Verificação pós-instalação (`make doctor` / `GET /api/system`): `torch.cuda.is_available()`,
   `torch.cuda.get_device_capability() == (12, 0)`, tensor de teste na GPU, `ctranslate2.get_cuda_device_count() > 0`.
6. Falha em qualquer verificação → `device=cpu` automático com aviso no Controle (sem bloquear o app).

## Consequências
- Positivas: uma linguagem no backend; nenhum runtime novo para instalar (Deno vem como wheel no venv); front sem build;
  contratos REST/SSE testáveis com curl/pytest; tudo que é do usuário em um diretório.
- Negativas / riscos:
  - Instalação baixa ~3 GB (torch + libs CUDA) e, no primeiro uso, ~2 GB de modelos (htdemucs_ft ~320 MB, whisper turbo ~1,6 GB).
  - torch fica em 2.13 (não 2.14) para alinhar CUDA 12 com CTranslate2. Quando CTranslate2 publicar wheel CUDA 13, migrar.
  - demucs está em modo manutenção; Roformer é a rota de upgrade.
  - yt-dlp quebra quando o YouTube muda → `make update-ytdlp` e mensagem clara no Controle.
  - Decodificar dois stems inteiros em memória: ~170 MB por música de 4 min (aceitável num PC; pré-carga da próxima dobra).
- Pré-requisitos para o usuário final: Linux com Python ≥ 3.12 (testado 3.14), ffmpeg, `make`; opcional driver NVIDIA ≥ 575
  (CUDA 12.9). Internet só para busca/download/LRCLIB/modelos.

## Notas de implementação (T-004, 2026-10-04)

Validado na instalação real deste PC (`make install`, Python 3.14.7, RTX 5060 sm_120, driver 615.71):

| Item | Resultado |
|------|-----------|
| Tempo de `make install` (rede ~10 MB/s, cache pip vazio) | **8 min 24 s** |
| Tamanho de `.venv/` | **7,6 GB** (a estimativa de ~3 GB era só o download; as libs `nvidia-*-cu12` descompactadas pesam mais) |
| Versões efetivas | torch 2.13.0+cu129 · ctranslate2 4.8.2 · faster-whisper 1.2.1 · demucs 4.1.0 · yt-dlp 2026.8.19 + deno 2.9.7 (wheel) · fastapi 0.142.2 · starlette 1.7.0 · uvicorn 0.54.0 · ruff 0.16.10 · pytest 9.1.1 |
| torchaudio | **não necessário** — demucs 4.1 não o puxa; não instalado |
| `torch.cuda` | ok, cc 12.0, tensor na GPU ok |
| CTranslate2 na CUDA | ok **com** o `LD_LIBRARY_PATH` do venv (faster-whisper `tiny` fp16 carregou e transcreveu na GPU) |
| CTranslate2 **sem** `LD_LIBRARY_PATH` e sem torch importado | `libcublas.so.12: cannot open shared object file` (embora `get_cuda_device_count()` = 1) |

Decisões/ajustes resultantes:
1. **`LD_LIBRARY_PATH` é obrigatório, não só salvaguarda.** `karaoke/__main__.py › prepare_environment` injeta
   `site-packages/nvidia/*/lib` (`system.cuda_env()`) antes de qualquer subprocesso; os workers (spawn) herdam. O
   `make doctor` testa CTranslate2 num processo limpo (sem torch) exatamente nessas condições. Importar torch antes
   continua valendo, mas não é mais o mecanismo principal.
2. `__main__` também põe `.venv/bin` no `PATH` (o yt-dlp acha o `deno` da wheel) e `HF_HOME`/`TORCH_HOME` em `data/models/`.
3. FastAPI 0.142 guarda routers incluídos como `_IncludedRouter` (`app.routes` não é plano): use
   `security.iter_api_routes(app)` para percorrer rotas. `request.scope["route"]` continua sendo a `APIRoute` original.
4. `/static/*` é uma rota (não `Mount`) para passar pelo `authorize` global como qualquer outra.
5. Lint: `E501` desligado no ruff check (o `ruff format` já limita o código a 110 colunas; docstrings longas são aceitas).
6. Aviso conhecido: Starlette 1.7 marca o uso de `httpx` no `TestClient` como deprecated (sugere `httpx2`); inofensivo
   por ora, reavaliar no release.

## Notas de implementação (FIX-002, 2026-10-04) — desvios aceitos do BUILD

Desvios do HANDOFF 0014 (backend-pipeline), aceitos pelo architect depois da revisão (`reports/review-1.md`):

| # | Desvio | Motivo | Reversível por |
|---|--------|--------|----------------|
| 1 | **Separação só com o especialista de voz** do `htdemucs_ft` (modelo `04573f0d`); `instrumental = mix − vocals` em vez de `drums+bass+other` | O bag tem pesos identidade (1 modelo por fonte): a voz sai igual à do bag completo, com ¼ de GPU e download (84 MB em vez de 320 MB). Equivale ao modo *two-stems* do demucs | `separate.run(..., full_bag=True)` |
| 2 | **Modelos em `data/models/hf`** (cache do HuggingFace) | O demucs 4.1 baixa de `adefossez/HTDemucs-ft` no HF, não do torch hub; `system.py`/`models.py` verificam esse cache | — |
| 3 | **Worker via `subprocess.Popen` + JSON por linha** no stdout, em vez de `multiprocessing` (`spawn`) + `mp.Queue` | Ambiente explícito (`cuda_env` + `model_env` + `HF_HUB_OFFLINE=1`), que também vale no `make dev`; cancelamento por `terminate()`; o worker sai sozinho se o pai morrer (watchdog `getppid`, testado com `kill -9`) | — |
| 4 | **ffmpeg decodifica o áudio para o Whisper** (float32 mono 16 kHz → numpy), em vez do PyAV | faster-whisper 1.2.1 + o PyAV instalado falham (`open() got an unexpected keyword argument 'metadata_errors'`) | voltar ao PyAV quando a combinação de versões funcionar |
| 5 | **Lock por data dir** `data/runner.lock` (`fcntl.flock`) | Uma 2ª instância no mesmo data dir não processa jobs (loga e segue servindo) | — (POSIX; no Windows exigiria `msvcrt.locking`) |

### Interfaces em `app.state` (combinadas nos HANDOFFs 0013/0014)

| Atributo | Dono | Uso |
|----------|------|-----|
| `config`, `db`, `bus`, `rate_limiter` | `app.py` (architect) | Config efetiva, `Database`, `EventBus`, `None` na v1 (ADR-0002) |
| `state_lock` | `services/library.py` | `threading.RLock` das transações de estado (`transaction(app)`) |
| `queue` | `services/queue.py` | `add(...)`, `song_changed(song_id)` (thread-safe; o runner chama no fim do job) |
| `player` | `services/player.py` | `PlayerService` (laço de tick como task asyncio) |
| `runner` | `jobs/runner.py` | `JobRunner`: `cancel(job_id)` (só sinaliza), `stop(timeout)` (mata o worker; usado no shutdown e no `hard_exit`) |
| `jobs_snapshot(actor)` | `jobs/runner.py` | Jobs ativos para o `hello` do SSE |
| `model_download` | `jobs/runner.py` | `{model_id, progress}` ou `None`, espelhado em tempo real |
| `runner_autostart` | testes | `False` evita a thread do runner nos testes de API |

Os consumidores leem os atributos opcionais com `getattr(app.state, ..., None)`.

### Encerramento e porta (QA H-01, M-01)
- `__main__.py` usa `KaraokeServer` (subclasse de `uvicorn.Server`). No 1º sinal chama `bus.close_all()` e o
  uvicorn tem `timeout_graceful_shutdown=3`. O 2º sinal, ou um watchdog de 10 s, força a saída depois de
  `runner.stop(2)`. Detalhes em ARCHITECTURE §8.1.
- `pick_port` usa `SO_REUSEADDR` (POSIX) no bind de teste. Assim a porta padrão volta a ser usada logo após um
  encerramento normal.
- Regressão: `tests/unit/test_main.py`, `tests/api/test_lifecycle.py` (processo real com 2 streams SSE, SIGINT e
  SIGTERM ≤ 3 s, reinício na mesma porta) e `tests/e2e/test_shutdown_e2e.py` (Palco e Controle no Chrome headless).
