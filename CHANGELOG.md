# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [0.2.0] — 2026-10-05

Letras no tempo certo: texto oficial, tempos tirados da voz e um editor de sincronia.

### Adicionado
- **Modos de letra** em Configurações (engrenagem › Letras): **Letra oficial sincronizada pela voz** (padrão) e
  **Letra online como está**. Dá para trocar o modo só para uma música no botão ▾ ao lado de *Preparar e enfileirar*.
- **Alinhamento pela voz:** o texto vem da letra oficial (LRCLIB com tempos, LRCLIB só com texto ou letra colada) e os
  tempos de linha e de palavra vêm da voz separada (Whisper). Linhas que não puderem ser alinhadas são estimadas
  entre as vizinhas e aparecem com "≈" no editor. Desvio médio medido de 22–140 ms em relação ao início real da voz.
- Busca de letra mais robusta no LRCLIB: várias consultas (título limpo, artista do canal, busca livre) e
  aproveitamento de registros que só têm texto.
- **Buscar letra oficial e sincronizar** no menu *Letra* da Biblioteca. Roda como job com progresso
  ("Sincronizando pela voz N%"); no fim, um aviso oferece *Desfazer* e *Conferir*. A letra anterior continua disponível.
- **Colar letra sem tempos** (texto puro): os tempos vêm da voz.
- **Editor de sincronia** (`E` no Controle com uma música tocando, botão *Editar sincronia* no mini-player ou menu
  *Letra*): áudio próprio com forma de onda da voz, lista com a linha atual destacada e prévia no estilo do Palco.
  - **Tocar e marcar** (`M`): aperte Espaço no início de cada linha, com contagem 3·2·1, compensação do tempo de
    reação e "Mover as seguintes" (`R`).
  - Ajuste fino por linha (±10 / ±100 ms), velocidade 0,5× / 0,75× / 1× sem mudar o tom, offset geral com prévia ao
    vivo e **Detectar automaticamente**.
  - Desfazer/refazer, *Descartar*, `Ctrl+S` para salvar como candidata **Manual** (a original não é apagada) e
    confirmação ao sair com alterações. Operável só pelo teclado, com legenda de atalhos.
- Chips de origem da letra: "Online · sincronizada pela voz", "Online", "Online · só texto", "Manual",
  "Colada · só texto" e "Extraída da voz (sem letra oficial)".
- Ferramenta de diagnóstico de sincronia (`python -m karaoke.tools.lyrics_sync_report`), que trabalha sobre uma
  cópia da pasta de dados.

### Alterado
- O texto da letra agora vem **sempre** da letra oficial quando ela existe. O texto do Whisper só é usado quando
  não há letra oficial nem colada; nesse caso a app sugere colar a letra.
- "Gerar com Whisper" / "Extrair da voz" saiu da interface e deu lugar a *Buscar letra oficial e sincronizar*.
- O ajuste de sincronia do modal *Letra* virou a faixa "Letra fora do tempo? [Editar sincronia] · ajuste rápido −/+".
  Os atalhos `[` / `]` do Palco continuam iguais.
- Banco de dados migrado sozinho para o schema v3 na primeira vez que a app sobe, sem perda de dados.

### Problemas conhecidos
- Cancelar uma música durante o 1º download de um modelo de IA trava a fila de preparo até reiniciar a app (M-N1).
  Veja o README.
- A forma de onda do editor carrega a voz da música inteira na memória do navegador (cerca de 20 a 40 MB para uma
  música de 4 min).
- Ainda não dá para arrastar os marcadores na forma de onda com o mouse. O ajuste é feito pelo teclado e pelos botões.
- Interno: o teste E2E `tests/e2e/test_control_e2e.py::test_library_filter_and_lyrics_paste_and_offset` está
  desatualizado em relação ao novo menu *Letra* (fora do `make test`).

## [0.1.0] — 2026-10-04

Primeira versão.

### Adicionado
- **Controle** (`/`) e **Palco** (`/stage`) no navegador, servidos localmente em `127.0.0.1`, com estado sincronizado
  em tempo real (SSE).
- Busca no YouTube (yt-dlp), com thumbnails, chips "Pronta", "Ao vivo" e "Vídeo longo", e aviso para vídeos com
  mais de 15 min.
- Preparo de músicas em fila: download, separação de voz por IA (demucs `htdemucs_ft`, GPU NVIDIA ou CPU) e letra
  sincronizada. Mostra progresso por etapa com ETA e retoma os jobs depois de reiniciar ou travar.
- Letras do LRCLIB com várias candidatas, fallback automático para transcrição com Whisper (tempo por palavra), opção
  de colar LRC e ajuste de sincronia por música (`[` / `]` no Palco).
- Biblioteca offline com busca local, chip de origem da letra e exclusão com o tamanho em disco.
- Fila com cantor, reordenação por arrastar, botões ou teclado, *Desfazer* e auto-avanço com "A seguir".
- Palco em tela cheia: letra preenchida palavra por palavra, controle de voz guia e volume, modo espelho para
  várias telas, atalhos de teclado e tela de atalhos (`?`).
- Temas claro e escuro no Controle e acessibilidade (foco visível, contraste AA, navegação por teclado).
- `make install` (escolhe o PyTorch CUDA ou CPU sozinho), `make start` (abre o navegador e mostra as URLs em
  destaque), `make doctor`, `make update-ytdlp`, `make dev` e `make test`.
- Encerramento seguro com `Ctrl+C`: fila, posição e jobs ficam salvos.

### Problemas conhecidos
- Cancelar uma música durante o 1º download de um modelo de IA trava a fila de preparo até reiniciar a app (M-N1).
  Veja o README.
