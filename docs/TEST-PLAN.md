# Plano de testes — Karaokê local

> Dono: qa-engineer · Ciclo `20261003-2339-karaoke` · Base: SPEC v1.2, contratos `docs/contracts/*`, DESIGN.md.
> Resultado da rodada atual: `.agents/runtime/20261003-2339-karaoke/reports/qa-1.md`.

## 1. Camadas e como rodar

| Camada | Onde | Comando | Requisitos |
|--------|------|---------|------------|
| Unidade + API (sem rede/GPU) | `tests/unit`, `tests/api` | `make test` | nada além do `.venv` |
| Lógica JS sem DOM (Palco + modelo do editor de sincronia) | `tests/e2e/test_*_test.js` (via `test_stage_js.py`) | `make test` | deno do `.venv` |
| **E2E navegador** (marcador `e2e`) | `tests/e2e/test_*_e2e.py` (fluxo, controle, FIX-1, shutdown, editor de sincronia) | `.venv/bin/python -m pytest -m e2e tests/e2e` | `pip install -e ".[e2e]"` (playwright) + Chrome/Chromium do sistema (`KARAOKE_E2E_CHROME` para outro caminho; `KARAOKE_E2E_HEADED=1` para ver a janela) e ffmpeg. **Não** baixa navegadores. Sem rede/GPU. |
| Pipeline real (marcadores `gpu`,`network`) | `tests/api/test_jobs_real.py` | `PATH=$PWD/.venv/bin:$PATH .venv/bin/python -m pytest -m "gpu and network" tests/api/test_jobs_real.py` | internet + GPU NVIDIA |
| Manual guiado (rede + GPU) | roteiro §4 | servidor real em data dir temporário | internet, GPU |

Os e2e sobem `python -m karaoke` de verdade (subprocesso) numa porta livre de 8840–8849, com `KARAOKE_DATA_DIR`
temporário e `KARAOKE_DEVICE=cpu`. As músicas são semeadas direto no SQLite com áudio Opus real gerado pelo ffmpeg
(tom senoidal) para o Palco decodificar de verdade; letras entram pela API `POST /lyrics/manual`.
O Palco abre num *contexto de navegador separado* do Controle (duas abas no mesmo contexto headless atrasam o
`click` da aba de trás em ~1 s — artefato do harness, ver docstring de `open_stage`).

## 2. Matriz história → critério → teste

Legenda: **A** = automático (arquivo::teste) · **M** = manual (roteiro §4) · **I** = inspeção de código/medição.

| Critério | Teste | Tipo |
|----------|-------|------|
| AC-01.1 busca ≤ 5 s, ≤ 15 resultados com thumb/título/canal/duração | `test_search.py` (contrato, mock yt-dlp) · roteiro M1 (busca real cronometrada no Controle) | A + M |
| AC-01.2 resultado já na biblioteca → "Pronta" + "Adicionar à fila" | `test_search.py::test_search_marks_library_long_and_live` · M1 (re-busca após preparar) | A + M |
| AC-01.3 erro de rede → mensagem + "Tentar de novo" | `test_search.py::test_search_errors` · M3 (Controle real em namespace sem rede `unshare -rn`) | A + M |
| AC-01.4 vídeo > 15 min avisa antes | `test_jobs.py::test_long_video_requires_confirmation` · M1 (modal "Este vídeo é longo") | A + M |
| AC-02.1 job aparece com etapa e % | `test_jobs.py`, `test_runner.py` · M1 (painel ao vivo) | A + M |
| AC-02.2 stems separados; 4 min ≤ 90 s na GPU | `test_jobs_real.py` · M1/M2 (`data/logs/timings.jsonl`) | A + M |
| AC-02.3 um job por vez, UI responsiva, tempo real | `test_runner.py` (serial) · M1 | A + M |
| AC-02.4 falha → erro legível + "Tentar de novo"; sem parciais | `test_runner.py` · M3/M4 (offline e vídeo inexistente) | A + M |
| AC-02.5 download do modelo como etapa com progresso | `test_runner.py` · M1 (htdemucs), M2 (whisper `small`) | A + M |
| AC-02.6 reiniciar retoma jobs sem corromper | `test_runner.py` (retomada) · M5 (`kill -9` e Ctrl+C no meio do job) | A + M |
| AC-03.1 LRCLIB com limpeza de título | `test_lrclib.py`, `test_titleclean.py` · M1 | A + M |
| AC-03.2 fallback Whisper com timestamps por palavra | `test_runner.py` (mock) · M2 (música autoral sem LRC) | A + M |
| AC-03.3 origem da letra visível | `test_control_e2e.py::test_library_filter_and_lyrics_paste_and_offset` · M2 | A + M |
| AC-03.4 trocar candidata / colar LRC / regenerar Whisper / rebuscar LRCLIB | `test_lyrics.py` · `test_control_e2e.py` (colar) · M2 (trocar, rebuscar, Whisper pela UI) | A + M |
| AC-03.5 offset ±50 ms salvo, inclusive por tecla | `test_player.py::test_offset_updates_song` · `test_flow_e2e.py::test_stage_keyboard_and_states` · `test_control_e2e.py` (modal) | A |
| AC-04.1 biblioteca com busca local | `test_songs.py` · `test_control_e2e.py` (filtro sem acento) | A |
| AC-04.2 excluir com confirmação remove arquivos | `test_songs.py` · `test_control_e2e.py::test_library_delete_with_confirmation_removes_files` | A |
| AC-04.3 persiste e funciona offline | `test_control_e2e.py::test_player_settings_persist_across_restart` · M3 (offline) | A + M |
| AC-04.4 posse da música (403 `not_owner`, UI esconde) | `test_songs.py`, `test_security.py` · `test_flow_e2e.py::test_ownership_rule_in_ui` | A |
| AC-05.1 enfileirar em processamento; pulados até prontos | `test_queue.py::test_autoplay_starts_ready_item_and_skips_not_ready`, `unit/test_queue.py` · `test_flow_e2e.py::test_stage_prep_and_error_scenes` | A |
| AC-05.2 reordenar (arrastar/botões/teclado), remover, limpar | `test_queue.py` · `test_control_e2e.py::test_queue_reorder_buttons_keyboard_drag_remove_clear` | A |
| AC-05.3 nome do cantor | `test_queue.py` · `test_flow_e2e.py::test_control_to_stage_flow_with_seeded_songs` | A |
| AC-05.4 fila igual nas telas ≤ 1 s, persiste | `test_events.py` (uvicorn real) · `test_queue.py::test_queue_persists_across_restart` · e2e (Controle↔Palco) | A |
| AC-05.5 posse do item da fila | `test_queue.py::test_remove_owner_rule_two_actors` · `test_flow_e2e.py::test_ownership_rule_in_ui` | A |
| AC-06.1 tela cheia, linha atual ≥ 5 vh | `test_flow_e2e.py::test_control_to_stage_flow_with_seeded_songs` (mede `font-size/innerHeight`) · M1 (1920×1080) | A + M |
| AC-06.2 desvio letra×áudio ≤ 100 ms (relógio do áudio) | `test_stage_lyrics_test.js` · I (`main.js › frame()` usa `engine.position` = `ctx.currentTime`) | A + I |
| AC-06.3 indicador em instrumentais > 5 s | `test_stage_lyrics_test.js` · M1 ("Volta em 2" em música real) | A + M |
| AC-06.4 "A seguir" e auto-avanço (com pausa do auto-avanço) | `test_player.py` · `test_flow_e2e.py` (auto e manual) | A |
| AC-06.5 fila vazia → tela de espera | `test_flow_e2e.py::test_stage_empty_queue_shows_waiting_screen` | A |
| AC-06.6 "Pressione Enter" na 1ª vez | idem | A |
| AC-07.1 transporte pelo Controle e teclado no Palco, `?` | `test_flow_e2e.py::test_stage_keyboard_and_states`, `::test_control_transport_next_prev_seek` | A |
| AC-07.2 comando ≤ 300 ms nos dois sentidos | `test_events.py::test_command_reflects_in_sse_under_300ms` · `test_flow_e2e.py::test_command_sync_control_stage_under_300ms` | A |
| AC-07.3 voz guia 0–100 %, padrão 0, persistida | `test_player.py::test_volume_guide_vocal_persist_in_settings` · `test_control_e2e.py::test_player_settings_persist_across_restart` · `::test_control_sliders_reflect_server_state_on_load` (xfail: QA-1 M-02) | A |
| AC-07.4 teclas `[` `]` com feedback | `test_flow_e2e.py::test_stage_keyboard_and_states` | A |
| DECISION 0006 espelho/claim com 2 Palcos | `test_player.py::test_last_claim_wins` · `test_flow_e2e.py::test_two_stages_mirror_and_claim` | A |
| Estados do Palco (erro ao tocar, aguardando preparo) | `test_flow_e2e.py::test_stage_prep_and_error_scenes` | A |
| Local-first no navegador (0 requests externos) | `test_flow_e2e.py::test_control_to_stage_flow_with_seeded_songs` (coleta `page.on("request")`) · M1 | A + M |
| Segurança local (Host/Origin, loopback, escopos) | `test_security.py`, `test_health.py` | A |

## 3. Não funcionais / checklists

| Item | Como |
|------|------|
| Instalação limpa | copiar o projeto sem `.venv`/`data` para diretório temporário → `make install` → `make start` → `make test` |
| Bind 127.0.0.1 | `ss -ltnp` com o app rodando; `KARAOKE_HOST=0.0.0.0` deve recusar |
| Requests externos do servidor | `grep` de URLs no código + amostragem `ss -tnp` durante um job real (só Google/YouTube e LRCLIB/Cloudflare; HF no download de modelo) |
| Offline | `unshare -rn` (namespace sem rede, só loopback): biblioteca, fila, Palco tocando, erros de busca/LRCLIB/job |
| Contraste | script que percorre os nós de texto visíveis e calcula a razão WCAG contra o fundo efetivo (claro e escuro) |
| Teclado/foco | percorrer o Controle com Tab, conferindo `outline`/`box-shadow` no elemento focado |
| Responsivo | 360/768/1280 px: `scrollWidth == innerWidth` + capturas |
| VRAM | `nvidia-smi` amostrado durante Whisper/separação (meta ≤ 8 GB) |
| Encerramento | Ctrl+C (SIGINT) com e sem Palco/Controle conectados; reinício imediato na mesma porta |

## 4. Roteiro manual (rede + GPU)

Servidor: `KARAOKE_PORT=884x KARAOKE_DATA_DIR=<tmp> KARAOKE_NO_BROWSER=1 .venv/bin/python -m karaoke`, Controle e Palco
em contextos Playwright separados (1440×900 e 1920×1080).

- **M1** Buscar "bohemian rhapsody" pelo Controle (cronometrar); verificar campos; abrir "Preparar e enfileirar"
  do vídeo de 22 min (modal de vídeo longo, Esc cancela); "Preparar e enfileirar" do oficial com cantor; acompanhar
  painel (modelo → baixando → separando → letra → pronta); Palco toca com letra; re-buscar → "Pronta".
- **M2** Letra pela UI: rebuscar no LRCLIB com termos editados, trocar candidata, "Gerar com Whisper" (1º uso baixa
  o large-v3-turbo, 1,6 GB); processar uma música autoral sem LRC (fallback automático); repetir com `whisper_model=small`
  para observar o progresso do download.
- **M3** Offline (`unshare -rn`): busca, biblioteca/filtro, tocar no Palco, preparar (erro + Tentar de novo), rebuscar letra.
- **M4** Job com vídeo inexistente: erro legível, "Tentar de novo", "Descartar", nada na biblioteca.
- **M5** Persistência: fila montada + job em `separating` → `kill -9` no servidor → reiniciar → fila idêntica, job
  retomado e concluído; repetir com Ctrl+C; recarregar o Controle no meio do job.

## 5. v0.2 — sincronia de letras (ciclo `20261004-1742-karaoke`, T-007)

Base: SPEC v0.2 (US-08, US-09, Revisão 2/CR-001). O roteiro manual do HANDOFF 0009 (8 passos) foi validado pelo
usuário; os testes abaixo codificam esse comportamento. Arrastar marcadores na onda foi descartado (sem teste).

| Critério | Teste | Tipo |
|----------|-------|------|
| AC-08.1 modos `aligned`/`online` persistidos; `transcribe` = alias | `test_lyrics_v02.py::test_lyrics_mode_setting`, `::test_settings_transcribe_is_alias_of_aligned` · roteiro 0009 passo 1 | A + M |
| AC-08.2 override do modo ao preparar | `test_lyrics_v02.py::test_job_lyrics_mode_override` · roteiro 0009 passo 2 | A + M |
| AC-08.3/08.7 alinhamento pela voz com texto oficial | `test_lyrics_v02.py::test_align_*`, `tests/unit/test_align.py` | A |
| AC-08.4 rótulos de origem | `test_lyrics_v02.py::test_candidate_flags_and_whisper_label` · `test_sync_editor_e2e.py` (lrclib → "manual" pela API) | A |
| AC-08.5 ações da biblioteca (menu Letra ▾) | `test_sync_editor_e2e.py::test_library_lyrics_menu_items_and_editor_from_menu` (4 itens + abre o editor) · roteiro 0009 passo 3 | A + M |
| AC-08.6 desvio ≤ 150 ms em músicas reais | `tests/unit/test_sync_report.py` + medição com GPU (fora do `make test`) | I |
| AC-08.8 busca de letra oficial robusta | `test_lyrics_v02.py::test_align_without_official_text_searches_first`, `tests/unit/test_lrclib.py` | A |
| AC-08.9 colar texto puro | API: `test_lyrics_v02.py::test_paste_plain_text_*` · UI: `test_control_e2e.py::test_library_filter_and_lyrics_paste_and_offset` (aba "Colar letra" aceita texto puro; o envio não é feito para não disparar o Whisper) · roteiro 0009 passo 4 | A + M |
| AC-09.1 editor pela biblioteca e pelo "tocando agora" (tecla `E`) | `test_sync_editor_e2e.py::test_sync_editor_adjust_undo_leave_dialog_and_save` (E) e `::test_library_lyrics_menu_items_and_editor_from_menu` (menu) | A |
| AC-09.2 tocar e marcar | lógica: `test_sync_model_test.js` (`markLine` sequencial, âncora, ripple, desfazer marcação) · fluxo com áudio: roteiro 0009 passo 6 (depende do relógio do `<audio>` tocando) | A + M |
| AC-09.3 ajuste fino ±10/±100 ms | `test_sync_model_test.js` (`shiftLine`, limites, ripple) · E2E: Shift+→ desloca as 3 linhas, ponto de alterada | A |
| AC-09.4 offset + Detectar automaticamente | API com mocks: `test_lyrics_v02.py::test_detect_offset_*` · aplicar/desfazer offset: `test_sync_model_test.js` (`snapshot`/`restore`/`changeCount`) · UI: roteiro 0009 passo 7 (precisa do Whisper) | A + M |
| AC-09.5 desfazer/refazer, salvar → Manual, confirmar ao sair | `test_sync_model_test.js` (`record` com agrupamento de 600 ms, limite 500, `undo`/`redo`) · E2E: Ctrl+Z, Esc → "Sair sem salvar?" (3 alterações) → Esc continua, Ctrl+S → PUT manual-timing → origem `manual` com `start_ms` [600, 3100, 6100], Esc fecha sem perguntar · API: `test_manual_timing_*` | A |
| AC-09.6 teclado | E2E do editor todo por teclado (E, Shift+→, Ctrl+Z, Esc, Ctrl+S) · tema claro/escuro: roteiro 0009 passo 8 | A + M |
| Validação antes de salvar (sem tempo, fora de ordem, depois do fim) | `test_sync_model_test.js` (`validateLines`, `outOfOrder`) · API: `test_manual_timing_validation` | A |

Rodar só a v0.2: `.venv/bin/python -m pytest -q -m e2e tests/e2e/test_sync_editor_e2e.py` e
`.venv/bin/deno test --quiet --no-check tests/e2e/test_sync_model_test.js` (ou `make test`, que inclui o JS).
