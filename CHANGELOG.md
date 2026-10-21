# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/). Versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Não lançado]

### Alterado
- **Indicador de voz fiel**: o ponto da régua mostra a altura real cantada (sem dobrar de oitava), com leitura
  suavizada: não treme em nota longa, acompanha vibrato e glissando e descarta saltos falsos de oitava.
- **Captação do microfone**: supressão de ruído e ganho automático desligados (cancelamento de eco continua ligado).
- **Tolerância estrita**: a nota só pontua a até ±50 cents (meio semitom) do alvo; uma oitava acima ou abaixo não vale.
- **Modo convidado no Windows (WSL)**: o QR mostra o IP do Windows na Wi-Fi/cabo, e não mais o IP interno `172.x`
  do WSL ([ADR-0006](docs/adr/0006-windows-wsl-lan-and-firewall.md)).

### Adicionado
- **Tecla `O`** no Palco: desloca a régua 0 → −1 → +1 oitava para quem canta em outro registro. A pontuação usa o
  mesmo alvo deslocado, e a escolha fica salva no Palco.
- **`karaoke.bat --configurar-rede` / `--desfazer-rede`** (Windows): libera a rede para os celulares dos convidados
  com `scripts/windows/setup-network.ps1`, que pede UAC e confirma cada passo. No Windows 11 usa o WSL *mirrored*, com
  backup do `.wslconfig`; no Windows 10, o `portproxy` com a tarefa `\Karaoke\AtualizarRede`. Inclui as regras de
  firewall do Windows e do Hyper-V (TCP 8765–8774, perfil Privado) e a opção de marcar a rede como Privada. O
  `karaoke.bat` oferece essa configuração uma vez, na primeira abertura.
- **`make firewall` / `make firewall-undo`** (Linux): liberam ou fecham 8765–8774/tcp no ufw ou firewalld. O
  `make install` oferece o mesmo passo no fim (S/n). Sem terminal interativo o passo é pulado, e a instalação nunca
  falha por causa dele.
- **Diagnósticos de rede no card do Modo convidado**: avisos detectáveis (encaminhamento faltando ou desatualizado,
  regra de firewall faltando, rede Pública, VPN), cada um com o comando para copiar.

## [0.4.0] — 2026-10-21

Pontuação e guia de notas no Palco, QR code também durante a música e novas fontes de letra (Vagalume e lyrics.ovh).

### Adicionado
- **Pontuação e guia de notas** no Palco: uma régua com as notas da voz original corre da direita para a esquerda
  acima da letra, até a linha "agora". O microfone do navegador mostra onde está a sua voz (ponto luminoso), a barra
  acende quando você acerta a nota e o placar no topo vai de 0 a 10.000. A pontuação depende do tempo afinado, não
  da velocidade da tela.
- Tolerância: ±1 semitom, e cantar uma ou mais oitavas acima ou abaixo também vale (vozes graves e agudas pontuam
  igual).
- **Pontuação final** no fim da música: uma faixa compacta no topo do "A seguir" (sem cobrir o próximo cantor) ou o
  cartão em tela cheia por 4 s quando a fila acabou. Com contagem do "A seguir" menor que 1,5 s, o cartão não aparece.
- **Tecla `S`** no Palco liga/desliga a pontuação (o HUD mostra "Pontuação: Ativada/Desativada").
- Interruptor **Pontuação e guia de notas** em Controle › Configurações › **Palco** (ligado por padrão). Vale para
  todos os Palcos abertos na hora.
- O microfone só é aberto com a pontuação ligada, no Palco que toca o som, e a análise é feita toda no navegador:
  o áudio do microfone nunca sai dele nem é tocado na caixa.
- As notas de cada música são extraídas da voz separada no fim do preparo (menos de 1 s por música) e guardadas em
  `data/songs/<id>/pitch.json`. Músicas preparadas em versões anteriores ganham as notas sozinhas na primeira vez
  que tocam.
- Avisos no Palco: "Notas indisponíveis para esta faixa" e "Permita o microfone no navegador para pontuar" (a música
  continua tocando nos dois casos).
- **QR code contínuo**: com o modo convidado ligado, o QR fica pequeno no canto inferior direito durante a música e
  em destaque entre as músicas.
- **Vagalume e lyrics.ovh** como fontes de letra, depois do LRCLIB e antes da voz (Whisper). Eles só são consultados
  quando o LRCLIB não tem uma letra que sirva. O texto só é aceito se título e artista conferem com a música, e os
  tempos vêm da voz, como numa letra colada.
- Rótulos de origem nas candidatas: "Vagalume · só texto" e "lyrics.ovh · só texto".
- **Buscar de novo** (em *Candidatas e colar LRC…*) consulta todas as fontes, LRCLIB primeiro, sem duplicar
  candidatas. Avisa quando uma fonte não respondeu ("Vagalume não respondeu") e só mostra erro de rede se nenhuma
  responder.
- Seção **Fontes de letra** nas Configurações: interruptor "Buscar letra em outras fontes (Vagalume, lyrics.ovh)"
  (ligado por padrão) e campo opcional "Chave da API do Vagalume", com *Salvar* e *Remover*. A chave fica só no
  computador e nunca é exibida nem devolvida pela API.

### Alterado
- O QR code do modo convidado não some mais durante a música (antes aparecia só entre as músicas).
- `GET /api/settings` ganhou `scoring_enabled`, `extra_lyrics_sources` e `vagalume_api_key_set`. `PATCH
  /api/settings` aceita `scoring_enabled`, `extra_lyrics_sources` e `vagalume_api_key` (`""` ou `null` remove a
  chave).
- Nova rota `GET /api/songs/{id}/pitch` (notas da voz; `404` se a música não existe, `notes: []` se não há notas).
- Quando o LRCLIB não acha a letra, o preparo pode levar alguns segundos a mais. Cada consulta desiste em ~5 s (no
  pior caso, cerca de 10 s por fonte) e na hora quando não há internet. Uma fonte com erro nunca faz o preparo falhar: a app passa para a próxima.
- Os textos de busca e de "nenhuma letra encontrada" citam todas as fontes ligadas.
- A busca manual de letra (`POST /api/songs/{id}/lyrics/search`) devolve `sources` com o estado de cada fonte e
  `502 all_sources_unavailable` quando todas falham por rede.
- A app passa a se conectar também a `vagalume.com.br` e `lyrics.ovh` (só título e artista). Desligue o interruptor
  para voltar a usar só o LRCLIB.
- `numpy` agora aparece explicitamente nas dependências (já era instalado junto com o demucs e o faster-whisper).
  Nada novo é baixado: quem já tem a v0.3 não precisa rodar `make install` de novo.

### Problemas conhecidos
- **A voz-guia que sai da caixa pode pontuar sozinha**: o microfone capta a voz original tocando. Deixe o microfone
  longe da caixa de som, ou abaixe/desligue a voz guia (`V`) para uma pontuação mais justa.
- Nas Configurações, depois de um *Tentar de novo* que dá certo, o foco do teclado cai no fundo do modal. Use `Tab`
  para voltar aos controles.
- **Letras.mus.br não é suportado**: o site bloqueia acesso automático. Use colar letra (menu *Letra* › *Colar
  letra…*) para músicas que só existem lá.
- Sem chave da API, o Vagalume depende da página pública do site e pode parar de trazer letras se o site mudar.
- A análise do microfone roda na thread da página do Palco. Em máquinas muito fracas, a régua pode engasgar; desligue
  a pontuação com `S` se isso acontecer.
- No Palco em modo espelho (sem som), a régua acompanha a posição do servidor e pode ficar ~0,1 s fora do tempo.
- Continuam valendo os problemas conhecidos da v0.3 (M-N1, muitos convidados abrindo `/guest` ao mesmo tempo e
  outros).

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
