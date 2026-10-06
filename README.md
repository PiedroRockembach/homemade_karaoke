# Karaokê local

Escolha qualquer música do YouTube e cante. A app baixa o áudio, usa IA para separar a voz do instrumental e
mostra a letra sincronizada em tela cheia na TV, tudo rodando no seu computador.

A app tem duas telas no navegador:

- **Controle** (`/`), no PC ou notebook: você busca músicas, monta a fila com o nome de quem canta, ajusta volume,
  voz guia e sincronia da letra.
- **Palco** (`/stage`), na TV em tela cheia: mostra a letra palavra por palavra, "A seguir" e quem é o próximo cantor.
  É também a tela que toca o som.

## Requisitos

| Item | Versão / observação |
|------|---------------------|
| Sistema | Linux x86_64 (testado em Linux 7.x) |
| Python | 3.12 ou mais novo, com `venv` (testado com **3.14**) |
| ffmpeg | qualquer versão recente, instalado pelo gerenciador de pacotes (ex.: `sudo pacman -S ffmpeg`, `sudo apt install ffmpeg`) |
| `make` e `bash` | já vêm na maioria das distribuições |
| GPU NVIDIA | **opcional**. Com ela uma música fica pronta em ~10–25 s. Sem ela tudo funciona na CPU, só que mais devagar |
| Internet | para buscar e baixar músicas e letras. Músicas já preparadas tocam offline |
| Disco | ~7,6 GB para o ambiente Python (`.venv/`, inclui PyTorch com CUDA) + modelos de IA baixados no 1º uso (84 MB do separador de voz demucs, até 1,6 GB do Whisper) + ~10 MB por música |

Não é preciso sudo nem instalar nada global além do ffmpeg: tudo fica dentro desta pasta.

## Instalar

```sh
make install
```

O script cria o ambiente em `.venv/`, detecta se há GPU NVIDIA (`nvidia-smi`) e escolhe a versão certa do PyTorch.
Leva de 2 a 10 minutos, dependendo da internet. Depois, se quiser, confira se está tudo certo:

```sh
make doctor
```

## Atalho na área de trabalho

- **Linux**: `./karaoke.sh --criar-atalho` cria o ícone "Karaokê" na área de trabalho e no menu de aplicativos.
  Clicar nele abre um terminal que roda `make install` (só na 1ª vez) e depois `make start`.
- **Windows** (via WSL, já que a app só roda em Linux): no `cmd`, dentro desta pasta, rode `karaoke.bat --criar-atalho`.
  Precisa de WSL com `make`, `python3`, `python3-venv` e `ffmpeg` instalados.

Para forçar uma nova instalação: `./karaoke.sh --reinstalar` ou `karaoke.bat --reinstalar`.

## Rodar

```sh
make start
```

O navegador abre sozinho no Controle. O terminal mostra os dois endereços em destaque:

```
Karaokê rodando em http://127.0.0.1:8765/
================================================================
  Controle (este PC):        http://127.0.0.1:8765/
  Palco (TV, tela cheia):    http://127.0.0.1:8765/stage
                             (no Palco, aperte Enter para liberar o som)

  Para sair: Ctrl+C
================================================================
```

1. Abra **http://127.0.0.1:8765/stage** numa janela do navegador e arraste-a para a TV (HDMI como segunda tela).
2. Aperte `F` para tela cheia e **Enter para liberar o áudio**. Os navegadores só tocam som depois de um gesto do usuário.
3. Use o Controle no PC.

Para parar: `Ctrl+C` no terminal. A fila, a posição da música e os jobs em andamento ficam salvos. No próximo
`make start` tudo continua de onde parou.

> Se a porta 8765 estiver ocupada, a app usa a próxima livre (8766, 8767…) e avisa no terminal.

## Como usar numa sessão

1. **Buscar**: na aba *Buscar* do Controle, digite artista ou música. Os resultados aparecem **enquanto você digita**:
   a Biblioteca filtra na hora e o YouTube busca quando você para de digitar (a partir de 3 letras). Enter busca na
   hora e `Esc` limpa. Atalho: `/` foca a busca.
2. **Preparar e enfileirar**: no resultado, clique em *Preparar e enfileirar* e informe quem vai cantar. A música aparece
   em *Processamento* (Baixando, Separando voz, Buscando letra) e entra na fila quando ficar pronta.
   *Só preparar* deixa a música pronta na Biblioteca sem colocá-la na fila.
3. **Cantar**: o Palco toca a fila na ordem. Com o auto-avanço ligado, a próxima começa após uma contagem "A seguir".
   Ajuste o volume e a **voz guia** (o quanto da voz original fica audível) no Controle ou pelos atalhos do Palco.
4. **Letra errada ou fora do tempo?** Na Biblioteca, abra o menu *Letra* da música, ou aperte `E` com ela tocando.
   Veja [Letras e sincronia](#letras-e-sincronia).
5. Reordene a fila arrastando pela alça, com os botões ↑/↓ ou com `Alt+↑/↓`. `Del` remove (com *Desfazer*).

As músicas preparadas ficam na **Biblioteca** e tocam na hora, sem internet, nas próximas vezes.

## Modo convidado (festa)

Os convidados escaneiam um QR code na TV e pedem músicas pelo próprio celular, sem instalar nada. Todos precisam
estar no **mesmo Wi-Fi** que o PC.

### Como ligar

1. Rode `make start` normalmente.
2. No Controle, no card **Modo convidado**, ligue o interruptor e confirme em **Ligar**. Não é preciso reiniciar a
   app. O card mostra o link do convite (com *Copiar*) e quantos convidados estão conectados.
3. O **QR code aparece no Palco entre as músicas**: na tela de espera, no "Pressione Enter", no "A seguir" e no
   "Aguardando preparo". Durante a música ele some.
4. O convidado escaneia o QR, digita o nome e já pode pedir.

Se o PC estiver em mais de uma rede (cabo e Wi-Fi, por exemplo), o card mostra um seletor: escolha a rede em que os
celulares estão. Trocar de rede desconecta quem já tinha entrado.

### O que o convidado pode fazer

- Buscar na Biblioteca (músicas prontas, que tocam na hora) e no YouTube. Os resultados aparecem enquanto digita: a
  Biblioteca filtra na hora e o YouTube responde logo depois que a pessoa para de digitar (a partir de 3 letras).
- Pedir músicas para o **fim da fila**, com o próprio nome ou o de outra pessoa como cantor.
- Ver a fila e a música atual ao vivo, a posição ("Sua vez em N") e o progresso das próprias músicas novas.
- Remover os **próprios** pedidos.

O convidado **não** controla o player, não reordena a fila, não remove pedidos dos outros e não mexe em letras nem em
configurações. Na fila do Controle e no "A seguir" do Palco aparece "pedido por <nome>", e você, o anfitrião, pode
remover qualquer pedido.

**Regras:** os pedidos entram sempre no **fim da fila** e vídeos com mais de **15 min** não podem ser pedidos pelo
convidado. Para evitar spam, há um limite de frequência: cerca de 30 buscas por minuto (com folga para rajadas) e
pedidos muito seguidos esperam alguns segundos. Se a busca passar do limite, aparece "Muitas buscas — de novo em N s",
os resultados ficam na tela e a busca é refeita sozinha. Não há limite de músicas por convidado.

### Novo QR × Encerrar convidados

- **Novo QR** troca o convite. O QR antigo para de funcionar, mas quem já entrou continua conectado. Use se o QR foi
  parar onde não devia (uma foto num grupo, por exemplo).
- **Encerrar convidados** desconecta todo mundo. Os pedidos continuam na fila. Para voltar, cada um escaneia o QR de
  novo.
- Desligar o interruptor encerra os convidados e fecha o acesso pela rede.

### Liberar a porta no firewall

O celular acessa a mesma porta da app, a que aparece no terminal (`8765` por padrão). Se essa porta estiver ocupada,
a app usa a próxima livre (8766, 8767…), e aí é essa que precisa ser liberada. Para a porta não mudar entre uma festa
e outra, fixe-a com `KARAOKE_PORT=8765 make start`.

Muitas distribuições não ativam firewall nenhum, e aí não há nada a fazer. Se ninguém conseguir entrar em 60 s, o
card mostra o comando certo para o seu sistema, com o botão *Copiar comando*.

**ufw** (Ubuntu, Mint, Pop!_OS…):

```sh
sudo ufw allow 8765/tcp          # libera
sudo ufw delete allow 8765/tcp   # fecha depois da festa
```

**firewalld** (Fedora, openSUSE…):

```sh
sudo firewall-cmd --add-port=8765/tcp      # libera até reiniciar o PC ou o firewalld
sudo firewall-cmd --remove-port=8765/tcp   # fecha
```

Para deixar liberado de forma permanente (vale depois de reiniciar):

```sh
sudo firewall-cmd --permanent --add-port=8765/tcp && sudo firewall-cmd --reload      # libera
sudo firewall-cmd --permanent --remove-port=8765/tcp && sudo firewall-cmd --reload   # fecha
```

Troque `8765` pela porta que aparece no terminal, se for outra. Com o modo convidado desligado, a app não escuta na
rede, mesmo com a porta liberada.

### Segurança

Use o modo convidado só na sua rede de casa, com gente de confiança. A conexão é HTTP simples, sem criptografia
(TLS). O modo volta a ficar desligado toda vez que a app reinicia.

### Solução de problemas do modo convidado

| Sintoma | Solução |
|---------|---------|
| O celular fica **"carregando" infinitamente** e a página nunca abre | Quase sempre é o **firewall** do PC descartando as conexões (o `ufw` faz isso sem avisar). Libere a porta que aparece no terminal: `sudo ufw allow <porta>/tcp` (ex.: `sudo ufw allow 8765/tcp`). Para outros firewalls, veja [Liberar a porta no firewall](#liberar-a-porta-no-firewall). |
| `ERR_ADDRESS_UNREACHABLE`, ou funciona por um tempo e depois para de responder | 1. O **Wi-Fi do PC com economia de energia** derruba as conexões que chegam. Desligue até reiniciar: `sudo iw dev wlan0 set power_save off` (troque `wlan0` pelo nome da sua placa, veja em `iw dev`). Para desligar de vez, crie `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` com as linhas `[connection]` e `wifi.powersave = 2` e reinicie o NetworkManager: `sudo systemctl restart NetworkManager`. 2. Confira se o celular não caiu para os **dados móveis** ou para **outra rede** (rede de convidados, 5 GHz de outro roteador). 3. Veja se o roteador tem **isolamento de clientes** ligado (linha abaixo). |
| O celular não abre a página ("não foi possível conectar") | 1. Confira se o celular está no **mesmo Wi-Fi** que o PC, e não nos dados móveis. 2. Libere a porta no firewall (veja acima). 3. Se o PC estiver em mais de uma rede, escolha no seletor do card a rede dos celulares. 4. Desligue a VPN do PC ou do celular. |
| Funciona no PC, mas nenhum celular conecta, mesmo com o firewall liberado | O celular pode estar numa **rede de convidados** do roteador ou com o **isolamento de clientes** (*AP isolation*, *client isolation*) ligado. Essas opções impedem um aparelho de falar com outro. Conecte os celulares à rede principal ou desligue o isolamento no roteador. |
| "Este QR não vale mais" | Alguém clicou em *Novo QR*. Escaneie o QR que está na TV agora. |
| "O anfitrião encerrou os convidados" | Alguém clicou em *Encerrar convidados* ou desligou o modo. Escaneie o QR de novo. |
| Depois de reiniciar a app, o QR antigo não abre mais | É esperado: a cada início o modo convidado volta **desligado** e o convite (QR) muda. Ligue o modo de novo no Controle e peça para todos escanearem o QR novo da TV. |
| Tela "Não consegui abrir a página neste navegador" | A página do convidado não carregou nesse celular. Toque em *Tentar de novo*. Se continuar, tire uma foto do quadro *Detalhes do erro* (traz a mensagem e a versão do navegador) e teste em outro navegador. Em navegadores muito antigos aparece "Navegador muito antigo". |
| Página em branco ou erro quando muita gente abre o QR ao mesmo tempo | Recarregue a página no celular. Acontece só com cerca de 10 celulares abrindo no mesmo instante. |
| "Muitas buscas — de novo em N s" ou "Calma! … Tente em N s" | Limite anti-spam (cerca de 30 buscas por minuto; pedidos muito seguidos). Na busca, os resultados ficam e ela se refaz sozinha; no pedido, espere a contagem terminar. |
| O QR não aparece no Palco | Ele só aparece entre as músicas e com o modo ligado. Confira o card no Controle. |

## Letras e sincronia

### Modos de letra

Na engrenagem do Controle, em **Letras**, escolha como a letra é obtida. A escolha é salva na hora.

| Modo | O que faz |
|------|-----------|
| **Letra oficial sincronizada pela voz** (padrão, recomendado) | O texto vem da letra oficial (LRCLIB) e os tempos de cada linha e palavra vêm da voz separada da música. Corrige letras online que estão adiantadas ou atrasadas |
| **Letra online como está** | Usa a letra sincronizada do LRCLIB sem mexer nos tempos. É mais rápido, mas a letra pode ficar fora do tempo |

Para mudar o modo **só para uma música**, clique no ▾ ao lado de *Preparar e enfileirar* e escolha o modo. Aparece o
chip "Letra: … ✕". O modal "Quem vai cantar?" também mostra o modo e permite alterá-lo.

O texto vem sempre da letra oficial quando ela existe. Se nenhuma for encontrada, a app usa o texto que o Whisper
entendeu da voz. O chip de origem mostra "Extraída da voz (sem letra oficial)", e a app sugere colar a letra.

Chips de origem: "Online · sincronizada pela voz", "Online", "Online · só texto", "Manual", "Colada · só texto" e
"Extraída da voz (sem letra oficial)".

### Corrigir a letra de uma música da Biblioteca

No menu **Letra ▾** da música:

- **Buscar letra oficial e sincronizar**: busca a letra oficial e alinha os tempos pela voz. O chip mostra
  "Sincronizando pela voz N%". No fim aparece um aviso com *Desfazer* (volta para a letra anterior) e *Conferir*
  (abre o editor).
- **Colar letra…**: cole só o texto, sem tempos. Os tempos vêm da voz e a letra entra em uso quando o job terminar.
  Um LRC com tempos também é aceito e é usado como está.
- **Candidatas e colar LRC…**: troca de candidata ou busca de novo com outros termos.
- **Editar sincronia…**: abre o editor.

### Editor de sincronia

Abra pelo menu *Letra*, pelo botão *Editar sincronia* do mini-player ou com a tecla **`E`** no Controle enquanto a
música toca. Nesse caso o Palco pausa e o editor começa na linha atual. O editor tem som próprio, forma de onda da voz
e uma prévia no estilo do Palco.

- **Ajustar** (modo padrão): escolha a linha com `↑`/`↓` e ajuste com `←`/`→` (±10 ms) ou `Shift+←`/`Shift+→`
  (±100 ms). `Enter` ou clicar na linha toca a partir de 1 s antes dela.
- **Tocar e marcar** (`M`): depois da contagem 3·2·1, aperte **Espaço** no início de cada linha. O cursor avança
  sozinho. `↑` ou `Backspace` volta uma linha para remarcar. Com **Mover as seguintes** ligado (`R`), as linhas
  seguintes andam junto. Dica: use a velocidade 0,75× (tecla `2`).
- **Offset geral**: slider e botões ±50 ms com prévia ao vivo, ou **Detectar automaticamente**, que sugere o offset
  a partir da voz. Se a sugestão for pouco confiável, a app recomenda *Buscar letra oficial e sincronizar*.
- Linhas com **≈** tiveram o tempo estimado. O ponto marca linhas alteradas, e o ⚠ marca linhas fora de ordem.
- **`Ctrl+S`** salva como candidata **Manual**. A letra original continua disponível na lista de candidatas.
  Ao sair com alterações, a app pergunta se você quer salvar.

| Tecla | Ajustar | Tocar e marcar |
|-------|---------|----------------|
| `Espaço` | tocar/pausar | marcar a linha |
| `Enter` | ouvir a linha (1 s antes) | marcar a linha |
| `K` | tocar/pausar | tocar/pausar |
| `↑` / `↓` | linha anterior/seguinte | `↑` volta uma linha |
| `Backspace` | — | volta uma linha |
| `←` / `→` | linha −10 / +10 ms | — |
| `Shift+←` / `Shift+→` | linha −100 / +100 ms | — |
| `Alt+←` / `Alt+→` | voltar/avançar 5 s | voltar/avançar 5 s |
| `[` / `]` | offset geral −50 / +50 ms | idem |
| `M` | alternar modo | alternar modo |
| `R` | liga/desliga Mover as seguintes | idem |
| `1` `2` `3` | velocidade 0,5× / 0,75× / 1× | idem |
| `Ctrl+Z` / `Ctrl+Shift+Z` (`Ctrl+Y`) | desfazer / refazer | idem |
| `Ctrl+S` | salvar | salvar |
| `Esc` | fechar (pergunta se houver alterações) | sair do modo |
| `?` | lista de atalhos | idem |

### Atalhos do Palco

| Tecla | Ação |
|-------|------|
| `Enter` | Começar / liberar o som · começar já no "A seguir" · tocar nesta tela (quando ela está em modo espelho) |
| `Espaço` | Tocar / pausar |
| `←` / `→` | Voltar / avançar 5 s |
| `↑` / `↓` | Volume ±5 % |
| `N` | Próxima música |
| `P` | Reiniciar a música (se passou de 3 s) / anterior |
| `−` / `+` (`=`) | Voz guia −10 % / +10 % |
| `V` | Liga/desliga a voz guia |
| `[` / `]` | Letra adianta / atrasa 50 ms |
| `A` | Liga/desliga o auto-avanço |
| `F` | Tela cheia |
| `?` | Mostrar/ocultar a lista de atalhos |
| `Esc` | Fecha os atalhos ou sai da tela cheia |

Controles remotos de TV que enviam setas, Enter e Espaço funcionam.

**No Controle:** `/` foca a busca · `Enter` busca · `Esc` fecha janelas · `E` abre o editor de sincronia da música
tocando · na fila: `Alt+↑/↓` move e `Del` remove.

## Onde ficam meus dados

Tudo fica em **`data/`**, dentro desta pasta:

| Caminho | Conteúdo |
|---------|----------|
| `data/karaoke.db` | biblioteca, letras, ajustes de sincronia, fila e estado do player (SQLite) |
| `data/songs/` | áudio de cada música (instrumental e voz separados) |
| `data/models/` | modelos de IA baixados no 1º uso. Podem ser apagados: são baixados de novo quando necessário |
| `data/cache/`, `data/tmp/` | caches e arquivos temporários |
| `data/logs/karaoke.log` | log da app, útil para relatar problemas |

Outra pasta de dados: `KARAOKE_DATA_DIR=/outro/lugar make start`.

### Backup

Pare a app (`Ctrl+C`) e copie a pasta `data/`. Se quiser economizar espaço, deixe `data/models/` de fora.

```sh
tar --exclude=data/models -czf karaoke-backup.tar.gz data/
```

Para restaurar, extraia o arquivo dentro da pasta do projeto, com a app parada.

## Solução de problemas

| Sintoma | Solução |
|---------|---------|
| `make start` diz "A app ainda não foi instalada" | Rode `make install` primeiro |
| Porta em uso / quero outra porta | A app tenta sozinha as 10 portas seguintes. Para fixar uma: `KARAOKE_PORT=8800 make start` |
| O navegador não abriu | Abra manualmente o endereço que aparece no terminal |
| Palco sem som | Clique na janela do Palco e aperte **Enter**. Só uma tela toca o som. As outras ficam em "modo espelho" e assumem o som com Enter |
| Preparar está lento / quero saber se a GPU está em uso | `make doctor` mostra se o PyTorch e o Whisper estão usando a GPU (CUDA) ou a CPU. Para forçar: `KARAOKE_DEVICE=cpu make start` (ou `cuda`) |
| Instalei numa máquina sem GPU e depois coloquei uma | Rode `make install` de novo para trocar o PyTorch pela versão CUDA |
| Busca ou download do YouTube parou de funcionar ("atualize o yt-dlp") | O YouTube muda com frequência. Rode `make update-ytdlp` e reinicie a app |
| "Sem conexão com o YouTube" | A busca precisa de internet. A Biblioteca continua funcionando offline |
| Letra adiantada ou atrasada | Menu *Letra* → *Buscar letra oficial e sincronizar*. Para um ajuste rápido: no Palco, `[` adianta e `]` atrasa 50 ms (fica salvo por música). Para corrigir linha a linha: `E` abre o editor de sincronia |
| Letra com palavras erradas | O chip diz "Extraída da voz (sem letra oficial)": menu *Letra* → *Colar letra…* e cole o texto oficial, sem tempos |
| Letra errada (outra música/versão) | Menu *Letra* → *Candidatas e colar LRC…* → trocar candidata ou *Buscar de novo* |
| Editor de sincronia sem forma de onda | Em músicas longas ou se o navegador não conseguir decodificar a voz, o editor mostra só a barra de tempo após alguns segundos. Todo o resto funciona |
| `ffmpeg` não encontrado | Instale pelo gerenciador de pacotes do sistema e rode `make doctor` |
| Algo estranho | Veja `data/logs/karaoke.log` |

### Problemas conhecidos

- **M-N1: cancelar uma música durante o 1º download de um modelo de IA trava a fila de preparo.** Se você cancelar
  ou excluir uma música enquanto aparece "Baixando modelo de IA · só no 1º uso", o download não é interrompido e
  nenhuma outra música é preparada. **Solução:** reinicie a app (`Ctrl+C` e `make start`). O job é retomado sozinho
  e nada se perde. Nesse caso o `Ctrl+C` pode levar até ~10 s para encerrar.
  **Recomendação:** na primeira música, espere o download do modelo terminar.
- A forma de onda do editor carrega a voz da música inteira na memória do navegador (cerca de 20 a 40 MB para uma
  música de 4 min).
- Ainda não dá para arrastar os marcadores na forma de onda com o mouse. Use o teclado ou os botões ±10 / ±100 ms.
- Modo convidado: com cerca de 10 celulares abrindo a página no mesmo instante, alguns podem ficar com a página em
  branco. Basta recarregar (veja a [solução de problemas do modo convidado](#solução-de-problemas-do-modo-convidado)).
- O tempo de preparo com GPU (~10–25 s) foi medido antes de o alinhamento da letra pela voz (Whisper) ficar sempre
  ligado. Com ele, pode demorar um pouco mais.
- Quando não há letra oficial, o texto vem do Whisper e pode ter palavras erradas ou perder o primeiro verso. Cole a
  letra oficial pelo menu *Letra*.

## Desenvolvimento

```sh
make dev       # servidor com reload automático (não abre o navegador), porta 8765
make test      # testes rápidos (unidade + API + JS do Palco), sem GPU nem internet
make test-all  # todos os testes, inclusive os que precisam de GPU e internet
make lint      # ruff + deno lint/fmt
make fmt       # formata o código
```

Testes de navegador (Playwright, usam o Chrome/Chromium instalado no sistema):

```sh
.venv/bin/pip install -e ".[e2e]"
.venv/bin/python -m pytest -m e2e tests/e2e
```

Arquitetura e decisões: `docs/ARCHITECTURE.md`, `docs/adr/`, contratos da API em `docs/contracts/`, design em
`docs/DESIGN.md`.

## Desinstalar

1. Pare a app (`Ctrl+C`).
2. Faça backup de `data/` se quiser guardar a biblioteca.
3. Apague a pasta do projeto. Isso remove `.venv/`, os modelos e as músicas. Nada é instalado fora dela.
4. Opcional: o `pip` pode ter deixado downloads em cache em `~/.cache/pip`. Para limpar: `python3 -m pip cache purge`.

## Uso do conteúdo do YouTube

A app é para **uso pessoal e privado**, como cantar em casa com amigos. Os áudios baixados e separados ficam só no
seu computador. Não redistribua esse material e respeite os direitos autorais e os Termos de Serviço do YouTube.
As letras vêm do [LRCLIB](https://lrclib.net), um banco aberto de letras sincronizadas.

## Privacidade

Por padrão, o servidor escuta só em `127.0.0.1`, então nenhum outro aparelho da rede consegue acessá-lo. A rede
local só passa a ter acesso enquanto o [modo convidado](#modo-convidado-festa) estiver ligado, e mesmo assim só à
página do convidado e ao que ela usa (o Controle, as configurações e os áudios continuam só no PC). Nada sai da sua
rede: os convidados conversam direto com o seu PC. A app só se conecta à internet para falar com o YouTube (busca e
download), o LRCLIB (letras) e o Hugging Face (download dos modelos no 1º uso). Não há telemetria.
