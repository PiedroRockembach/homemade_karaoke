# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [0.3.0] — 2026-10-05

Modo convidado: os convidados escaneiam um QR code na TV e pedem músicas pelo celular, na mesma rede Wi-Fi.

### Adicionado
- **Modo convidado** no Controle (card *Modo convidado*): liga e desliga sem reiniciar a app e começa sempre
  desligado. Ao ligar, a app passa a aceitar conexões também no IP da rede local, na mesma porta. O card mostra o
  link (com *Copiar*), quantos convidados estão conectados, um seletor de rede quando o PC tem mais de uma e uma dica
  de firewall com o comando pronto se ninguém entrar em 60 s.
- **QR code no Palco** entre as músicas (tela de espera, "Pressione Enter", "A seguir" e "Aguardando preparo"). Ele
  nunca aparece durante a música.
- **Página do convidado** (`/guest`), feita para celular, sem instalar nada: entra com o nome, busca na Biblioteca e
  no YouTube, pede músicas para o fim da fila (o cantor pode ser outra pessoa), acompanha a fila e a música atual ao
  vivo, vê o progresso das próprias músicas novas e remove os próprios pedidos. Funciona nos temas claro e escuro.
- Regras para o convidado: os pedidos entram sempre no fim da fila, vídeos com mais de 15 min não podem ser pedidos e
  há um limite de frequência (anti-spam) para buscas e requisições.
- **"Pedido por <nome>"** na fila do Controle e no "A seguir" do Palco, e um aviso "<nome> pediu '…'" no Controle.
- **Novo QR** troca o convite (quem já entrou continua). **Encerrar convidados** desconecta todos (os pedidos ficam
  na fila).
- **Busca enquanto digita**, no Controle e na página do convidado: a Biblioteca filtra na hora e o YouTube busca
  ~0,7 s depois da última tecla (a partir de 3 letras), cancelando a busca anterior. Os resultados atuais ficam na
  tela com "buscando…" até a nova resposta. Enter continua buscando na hora; `Esc` limpa (Controle).
- **Tela de erro legível na página do convidado**: se a página não carregar no navegador do celular, aparece "Não
  consegui abrir a página neste navegador" com os detalhes do erro e *Tentar de novo*, em vez de uma tela branca.
  "Carregando…" já aparece antes do JavaScript.

### Alterado
- O anfitrião modera a fila: pode remover qualquer item, inclusive os pedidos dos convidados. Esses itens não têm
  *Desfazer*, porque re-adicioná-los apagaria o "pedido por". Para excluir da Biblioteca uma música que um convidado
  pediu, remova antes o pedido da fila.
- Corpo de requisição com tamanho máximo: 16 KiB pela rede local e 8 MiB no próprio PC. Acima disso, a resposta é
  `413`.
- Nova dependência: `segno` (gera o QR code localmente, sem internet). O `make install` já a instala.
- `KARAOKE_HOST` continua aceitando só `127.0.0.1`. O acesso pela rede abre apenas pelo toggle do modo convidado.
- Limite de busca do convidado mais folgado: cerca de **30 buscas por minuto**, com rajada de 10. Ao passar do limite
  durante a digitação, os resultados ficam, aparece "Muitas buscas — de novo em N s" e a busca se refaz sozinha.
  Não há limite de músicas por convidado.

### Problemas conhecidos
- **Muitos convidados abrindo a página ao mesmo tempo** (N-2): com cerca de 10 celulares carregando `/guest` no mesmo
  instante, alguns podem receber `503` e ficar com a página em branco. Basta recarregar.
- "Trocar nome" na página do convidado é só exibição. O nome do cantor pode ser editado em cada pedido.
- O chip "Preparando" das músicas que outra pessoa pediu aparece sem a porcentagem.
- Herdado da v0.2: o tempo de preparo informado no README (~10–25 s com GPU) foi medido antes do alinhamento pela
  voz com o Whisper ficar sempre ligado e não foi medido de novo.
- Continuam valendo os problemas conhecidos da v0.2 (M-N1 e outros).

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
