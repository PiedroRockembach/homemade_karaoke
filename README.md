# Karaokê local

Escolha qualquer música do YouTube e cante. A app baixa o áudio, usa IA para separar a voz do instrumental e
mostra a letra sincronizada em tela cheia na TV, tudo rodando no seu computador.

A app tem duas telas no navegador:

- **Controle** (`/`), no PC ou notebook: você busca músicas, monta a fila com o nome de quem canta, ajusta volume,
  voz guia e sincronia da letra.
- **Palco** (`/stage`), na TV em tela cheia: mostra a letra palavra por palavra, "A seguir" e quem é o próximo cantor.
  É também a tela que toca o som e, se você quiser, dá **pontos** para quem canta, com uma régua das notas da música.

## Requisitos

| Item | Versão / observação |
|------|---------------------|
| Sistema | Linux x86_64 (testado em Linux 7.x) |
| Python | 3.12 ou mais novo, com `venv` (testado com **3.14**) |
| ffmpeg | qualquer versão recente, instalado pelo gerenciador de pacotes (ex.: `sudo pacman -S ffmpeg`, `sudo apt install ffmpeg`) |
| `make` e `bash` | já vêm na maioria das distribuições |
| GPU NVIDIA | **opcional**. Com ela uma música fica pronta em ~10–25 s. Sem ela tudo funciona na CPU, só que mais devagar |
| Microfone | **opcional**, só para a [pontuação](#pontuação-e-guia-de-notas). Qualquer microfone que o navegador do Palco reconheça |
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
3. O **QR code aparece no Palco**: em destaque entre as músicas (tela de espera, "Pressione Enter", "A seguir" e
   "Aguardando preparo") e pequeno no canto inferior direito durante a música, para quem chegar depois.
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

### Liberar a porta no firewall (Linux)

O celular acessa a mesma porta da app, a que aparece no terminal (`8765` por padrão). Se essa porta estiver ocupada,
a app usa a próxima livre (8766, 8767… até 8774). Por isso a liberação automática cobre a faixa **8765–8774**. Para a
porta não mudar entre uma festa e outra, fixe-a com `KARAOKE_PORT=8765 make start`.

No fim do `make install`, se houver **ufw** ou **firewalld** ativo, a instalação pergunta
`Liberar as portas 8765-8774/tcp no <firewall> para os convidados? (pede sudo) [S/n]`. Sem terminal interativo, o passo
é pulado, e a instalação nunca falha por causa dele. Para fazer depois, ou desfazer:

```sh
make firewall        # libera 8765-8774/tcp no ufw ou firewalld (pede sudo)
make firewall-undo   # fecha de novo
```

Para responder sem pergunta (scripts): `KARAOKE_FIREWALL=yes make install` (ou `no`). Muitas distribuições não ativam
firewall nenhum. Nesse caso o script avisa "nenhum firewall ativo detectado" e não faz nada. Se ninguém conseguir
entrar em 60 s, o card do Controle mostra o comando certo para o seu sistema, com o botão *Copiar comando*.

Comandos equivalentes, à mão:

```sh
# ufw (Ubuntu, Mint, Pop!_OS…)
sudo ufw allow 8765:8774/tcp comment 'karaoke convidados'   # libera
sudo ufw delete allow 8765:8774/tcp                         # fecha
# firewalld (Fedora, openSUSE…)
sudo firewall-cmd --permanent --add-port=8765-8774/tcp && sudo firewall-cmd --reload      # libera
sudo firewall-cmd --permanent --remove-port=8765-8774/tcp && sudo firewall-cmd --reload   # fecha
```

Com o modo convidado desligado, a app não escuta na rede, mesmo com a porta liberada.

### Modo convidado no Windows (WSL)

No Windows a app roda dentro do WSL, e por padrão o WSL fica numa rede interna (IP `172.x`) que o celular não
alcança. Além disso, o Firewall do Windows bloqueia a entrada, ainda mais com a rede marcada como **Pública**. Por isso
há um passo de rede, feito uma única vez:

```bat
karaoke.bat --configurar-rede
```

Na primeira vez que você abre o `karaoke.bat`, ele já pergunta
`Configurar agora a rede para os celulares dos convidados? [S,N]`. Se responder N (ou negar a permissão de
administrador), ele não pergunta mais e você pode rodar o comando acima quando quiser. O Windows pede permissão de administrador (UAC). O script mostra o plano e
**pergunta antes de cada passo**:

1. **Rede do WSL**:
   - **Windows 11 22H2 ou mais novo, com WSL 2.0+ → modo *mirrored* (recomendado)**. O WSL passa a usar os mesmos IPs
     do Windows. O script acrescenta `networkingMode=mirrored` na seção `[wsl2]` de `%UserProfile%\.wslconfig` (sem
     apagar o que já existe), guarda um backup em `%LOCALAPPDATA%\Karaoke\wslconfig.bak` e oferece `wsl --shutdown`
     para valer na hora. **Atenção**: isso muda a rede de todas as suas distros WSL e fecha o que estiver aberto nelas.
   - **Windows 10 (ou se você recusar o mirrored) → encaminhamento de portas** (`netsh interface portproxy`) do IP do
     Windows na Wi-Fi/cabo para o IP do WSL, portas 8765–8774 (nunca em `0.0.0.0`). Como o IP do WSL muda a cada boot,
     o script cria a tarefa agendada `\Karaoke\AtualizarRede`, que o `karaoke.bat` roda antes de iniciar, **sem pedir
     UAC de novo**.
2. **Firewall**: regra `Karaoke-TCP-In` no Firewall do Windows (entrada TCP 8765–8774, só da sub-rede local, perfil
   **Privado**). Você pode incluir redes Públicas, mas só se confiar em todos dessa rede. No modo mirrored entra também
   a regra `Karaoke-WSL-TCP-In` no firewall do Hyper-V, que filtra o que chega ao WSL, com o mesmo perfil e, quando
   o Windows aceita, também restrita à sub-rede local.
3. **Tipo de rede**: se a sua Wi-Fi estiver como **Pública**, o script oferece marcá-la como **Privada**.

O que foi feito fica registrado em `%LOCALAPPDATA%\Karaoke\rede.json`. Rodar de novo é seguro: as regras são recriadas,
nunca duplicadas. Para desfazer tudo (regras, encaminhamento, tarefa e, se foi o script que mudou, o `.wslconfig`, com
confirmação, e o serviço "Auxiliar IP" que o encaminhamento precisou ligar):

```bat
karaoke.bat --desfazer-rede
```

O tipo de rede (Privada/Pública) não é revertido. Para mudar: *Configurações > Rede e Internet > Wi-Fi (ou
Ethernet) > propriedades da rede > Tipo de perfil de rede*.

O card do Controle avisa quando detecta algo errado no Windows (encaminhamento faltando ou desatualizado, regra de
firewall faltando, rede Pública) e mostra o comando para copiar. Se o QR ainda mostrar um IP `172.x`, defina o IP do
Windows à mão (veja em `ipconfig`): `wsl -- env KARAOKE_GUEST_ADVERTISE=192.168.1.16 make start`.

**Comandos manuais equivalentes** (PowerShell **como administrador**):

```powershell
# Firewall do Windows (todas as versões)
New-NetFirewallRule -Name Karaoke-TCP-In -DisplayName "Karaoke convidados (TCP 8765-8774)" -Group Karaoke `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765-8774 -Profile Private -RemoteAddress LocalSubnet
# Windows 11 22H2+: em %UserProfile%\.wslconfig, na seção [wsl2], ponha  networkingMode=mirrored  e rode  wsl --shutdown
New-NetFirewallHyperVRule -Name Karaoke-WSL-TCP-In -DisplayName "Karaoke convidados WSL" `
  -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Direction Inbound -Action Allow -Protocol TCP -LocalPorts 8765-8774
# Windows 10: encaminhar a porta (troque os IPs; o do WSL sai de:  wsl -e sh -c "ip -4 addr show eth0")
netsh interface portproxy add v4tov4 listenaddress=192.168.1.16 listenport=8765 connectaddress=172.24.5.3 connectport=8765
# Marcar a rede como Privada
Get-NetConnectionProfile                       # veja o InterfaceIndex
Set-NetConnectionProfile -InterfaceIndex 12 -NetworkCategory Private
# Desfazer
Remove-NetFirewallRule -Group Karaoke; Remove-NetFirewallHyperVRule -Name Karaoke-WSL-TCP-In
netsh interface portproxy delete v4tov4 listenaddress=192.168.1.16 listenport=8765
```

### Segurança

Use o modo convidado só na sua rede de casa, com gente de confiança. A conexão é HTTP simples, sem criptografia
(TLS). O modo volta a ficar desligado toda vez que a app reinicia.

### Solução de problemas do modo convidado

| Sintoma | Solução |
|---------|---------|
| O celular fica **"carregando" infinitamente** e a página nunca abre | Quase sempre é o **firewall** do PC descartando as conexões (o `ufw` faz isso sem avisar). No Linux, rode `make firewall` (libera 8765–8774 no ufw/firewalld). No Windows, `karaoke.bat --configurar-rede`. Para outros firewalls, veja [Liberar a porta no firewall](#liberar-a-porta-no-firewall-linux). |
| `ERR_ADDRESS_UNREACHABLE`, ou funciona por um tempo e depois para de responder | 1. O **Wi-Fi do PC com economia de energia** derruba as conexões que chegam. Desligue até reiniciar: `sudo iw dev wlan0 set power_save off` (troque `wlan0` pelo nome da sua placa, veja em `iw dev`). Para desligar de vez, crie `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` com as linhas `[connection]` e `wifi.powersave = 2` e reinicie o NetworkManager: `sudo systemctl restart NetworkManager`. 2. Confira se o celular não caiu para os **dados móveis** ou para **outra rede** (rede de convidados, 5 GHz de outro roteador). 3. Veja se o roteador tem **isolamento de clientes** ligado (linha abaixo). |
| O celular não abre a página ("não foi possível conectar") | 1. Confira se o celular está no **mesmo Wi-Fi** que o PC, e não nos dados móveis. 2. Libere a porta no firewall (veja acima). 3. Se o PC estiver em mais de uma rede, escolha no seletor do card a rede dos celulares. 4. Desligue a VPN do PC ou do celular. |
| Funciona no PC, mas nenhum celular conecta, mesmo com o firewall liberado | O celular pode estar numa **rede de convidados** do roteador ou com o **isolamento de clientes** (*AP isolation*, *client isolation*) ligado. Essas opções impedem um aparelho de falar com outro. Conecte os celulares à rede principal ou desligue o isolamento no roteador. |
| **Windows**: o QR mostra um IP `172.x` (ou nada abre no celular) | O WSL está na rede interna dele. Rode `karaoke.bat --configurar-rede` (veja [Modo convidado no Windows](#modo-convidado-no-windows-wsl)). Se o QR continuar com `172.x`, use `KARAOKE_GUEST_ADVERTISE` com o IP do Windows (mesma seção). |
| **Windows**: funcionava e parou depois de reiniciar o PC | No Windows 10 (encaminhamento de portas), o IP do WSL muda a cada boot. Abra a app **pelo `karaoke.bat`**, que atualiza o encaminhamento. Se o card avisar "IP antigo do WSL", feche e abra de novo pelo `karaoke.bat`. Se a tarefa sumiu, rode `karaoke.bat --configurar-rede`. |
| **Windows**: a rede aparece como **Pública** | O Windows bloqueia quem chega por redes Públicas. Rode `karaoke.bat --configurar-rede` e aceite marcar como Privada, ou mude em *Configurações > Rede e Internet > propriedades da rede*. |
| **Windows**: tudo configurado e mesmo assim não conecta | Um **antivírus com firewall próprio** (Kaspersky, ESET, Norton, Avast, McAfee, Bitdefender…) substitui o Firewall do Windows. Libere as portas TCP 8765–8774 (ou o "Karaoke") no firewall do antivírus, ou desligue a proteção de rede dele durante a festa. |
| O PC está em uma banda e o celular em outra (2,4 GHz × 5 GHz, ou repetidor) | Normalmente funciona, mas alguns roteadores separam as bandas ou o repetidor como redes isoladas. Conecte o celular na mesma rede (mesmo nome) do PC ou desative a separação no roteador. |
| VPN ligada no PC ou no celular | A VPN desvia o tráfego e o celular não acha o PC. Desligue a VPN durante a festa (o card avisa quando há VPN no PC). |
| "Este QR não vale mais" | Alguém clicou em *Novo QR*. Escaneie o QR que está na TV agora. |
| "O anfitrião encerrou os convidados" | Alguém clicou em *Encerrar convidados* ou desligou o modo. Escaneie o QR de novo. |
| Depois de reiniciar a app, o QR antigo não abre mais | É esperado: a cada início o modo convidado volta **desligado** e o convite (QR) muda. Ligue o modo de novo no Controle e peça para todos escanearem o QR novo da TV. |
| Tela "Não consegui abrir a página neste navegador" | A página do convidado não carregou nesse celular. Toque em *Tentar de novo*. Se continuar, tire uma foto do quadro *Detalhes do erro* (traz a mensagem e a versão do navegador) e teste em outro navegador. Em navegadores muito antigos aparece "Navegador muito antigo". |
| Página em branco ou erro quando muita gente abre o QR ao mesmo tempo | Recarregue a página no celular. Acontece só com cerca de 10 celulares abrindo no mesmo instante. |
| "Muitas buscas — de novo em N s" ou "Calma! … Tente em N s" | Limite anti-spam (cerca de 30 buscas por minuto; pedidos muito seguidos). Na busca, os resultados ficam e ela se refaz sozinha; no pedido, espere a contagem terminar. |
| O QR não aparece no Palco | Ele só aparece com o modo ligado (durante a música, fica pequeno no canto inferior direito). Confira o card no Controle. |

## Pontuação e guia de notas

Com a pontuação ligada, o Palco mostra acima da letra uma **régua com as notas da voz original**, que correm da
direita para a esquerda até a linha "agora". Quem canta vê a própria voz como um ponto luminoso: quando ele bate na
nota, a barra acende e o **placar** no topo sobe. O máximo é 10.000 pontos. No fim da música aparece a **Pontuação
Final**: uma faixa no topo do "A seguir" ou, se a fila acabou, um cartão em tela cheia por 4 s.

### Como ligar

- Vem **ligada** por padrão. No Controle: engrenagem › **Configurações** › seção **Palco** › interruptor
  **Pontuação e guia de notas**. Vale para todos os Palcos abertos na hora.
- No Palco, a tecla **`S`** liga e desliga (o canto da tela mostra "Pontuação: Ativada/Desativada").
- Desligada, a régua, o placar e o microfone somem; a letra e o som continuam iguais.

### Microfone

- Na primeira música com notas, o navegador do Palco pede permissão para usar o microfone. Clique em **Permitir**.
  O Palco roda em `http://127.0.0.1`, que o navegador trata como endereço seguro, então o pedido aparece normalmente.
- Se você negar, aparece "Permita o microfone no navegador para pontuar" e a música segue sem placar. Para liberar
  depois: clique no ícone de cadeado/microfone na barra de endereço, permita e aperte `S` duas vezes (desliga e liga).
- O microfone só abre com a pontuação ligada, no Palco que toca o som. Um Palco em modo espelho mostra a régua, mas
  não escuta nem pontua.
- **O áudio do microfone nunca sai do navegador**: a análise é feita ali mesmo, nada é gravado nem enviado ao
  servidor, e a sua voz não é tocada na caixa.

### Dicas para pontuar

- **O ponto mostra a altura real da sua voz**, suavizado para não tremer. A nota só conta quando você está a até
  **meio semitom (±50 cents)** dela. Cantar uma oitava acima ou abaixo **não** pontua.
- **Canta em outro registro?** Aperte **`O`** no Palco para deslocar a régua: oitava original → 1 oitava abaixo →
  1 oitava acima. O canto da tela mostra "Régua: …" e a régua exibe "−1 oitava" ou "+1 oitava". A escolha fica salva
  neste Palco e vale para as próximas músicas.
- **Microfone baixo?** A captação desliga a supressão de ruído e o ganho automático do navegador para não distorcer a
  voz. Se o ponto não acender mesmo cantando, aumente o volume de entrada do microfone nas configurações de som do
  sistema.
- **Deixe o microfone longe da caixa de som.** A voz guia que sai da caixa também é captada e pode pontuar sozinha.
  Para uma pontuação mais justa, abaixe ou desligue a voz guia (`V`).
- Os pontos dependem do tempo cantado na nota, não da velocidade da tela nem do computador.

### De onde vêm as notas

As notas são extraídas da voz separada no fim do preparo de cada música. Isso soma **menos de 1 s** por música. As
músicas preparadas em versões anteriores ganham as notas sozinhas na primeira vez que tocam (também em menos de
1 s). Se não for possível extrair as notas, o Palco mostra "Notas indisponíveis para esta faixa" e toca a música
normalmente, só com a letra.

## Letras e sincronia

### Modos de letra

Na engrenagem do Controle, em **Letras**, escolha como a letra é obtida. A escolha é salva na hora.

| Modo | O que faz |
|------|-----------|
| **Letra oficial sincronizada pela voz** (padrão, recomendado) | O texto vem da letra oficial (LRCLIB) e os tempos de cada linha e palavra vêm da voz separada da música. Corrige letras online que estão adiantadas ou atrasadas |
| **Letra online como está** | Usa a letra sincronizada do LRCLIB sem mexer nos tempos. É mais rápido, mas a letra pode ficar fora do tempo |

Para mudar o modo **só para uma música**, clique no ▾ ao lado de *Preparar e enfileirar* e escolha o modo. Aparece o
chip "Letra: … ✕". O modal "Quem vai cantar?" também mostra o modo e permite alterá-lo.

O texto vem sempre da letra oficial quando ela existe (veja [De onde vem a letra](#de-onde-vem-a-letra)). Se
nenhuma fonte tiver a letra, a app usa o texto que o Whisper entendeu da voz. O chip de origem mostra "Extraída da voz (sem letra oficial)", e a app sugere colar a letra.

Chips de origem: "Online · sincronizada pela voz", "Online", "Online · só texto", "Manual", "Colada · só texto" e
"Extraída da voz (sem letra oficial)".

### De onde vem a letra

Ao preparar uma música, a app procura a letra nesta ordem e para na primeira que servir:

1. **LRCLIB**: letra sincronizada (com tempos).
2. **Vagalume**: só o texto. Ajuda muito com músicas brasileiras que não estão no LRCLIB.
3. **lyrics.ovh**: só o texto.
4. **Voz (Whisper)**: se nenhuma fonte tiver a letra, o texto é transcrito da voz separada.

O Vagalume e o lyrics.ovh só são consultados quando o LRCLIB não tem uma letra que sirva. Um resultado só é aceito se
o título e o artista conferem com a música; se não conferem, é descartado, para não pegar a letra de outra música.
Os textos dessas fontes não têm tempos: os tempos vêm da voz, como uma letra colada. Na lista de candidatas eles
aparecem como "Vagalume · só texto" e "lyrics.ovh · só texto".

Se uma fonte estiver fora do ar, demorar ou tiver mudado o site, a app passa para a próxima, e o preparo nunca falha
por causa disso. Sem internet, as fontes extras desistem na hora.

Em **Candidatas e colar LRC…**, *Buscar de novo* consulta **todas** as fontes (LRCLIB primeiro) e acrescenta à lista
só as letras novas. Se uma fonte não responder, aparece um aviso (por exemplo, "Vagalume não respondeu") e você
recebe o que as outras trouxeram. O erro de rede só aparece se nenhuma fonte responder.

**Configurar** (engrenagem do Controle, seção **Fontes de letra**; tudo é salvo na hora):

- **Buscar letra em outras fontes (Vagalume, lyrics.ovh)**: vem ligado. Desligado, a app só usa o LRCLIB e a voz,
  como nas versões anteriores.
- **Chave da API do Vagalume (opcional)**: sem chave, a app lê a página pública da música no Vagalume. Isso funciona
  hoje, mas pode parar se o Vagalume mudar o site. Nesse caso a app passa para o lyrics.ovh e a voz. Com chave, a app
  usa a API oficial, que é mais estável. A chave é gratuita: crie uma conta no Vagalume e gere a chave na área de API
  para desenvolvedores ([api.vagalume.com.br](https://api.vagalume.com.br)). Cole no campo e clique em *Salvar* (ou
  `Enter`). A chave fica só neste computador, no banco de dados local, e nunca é exibida de novo. *Remover* apaga a
  chave. Se a chave for inválida, a app volta a usar a página pública.

**O que sai da máquina:** para o LRCLIB, o Vagalume e o lyrics.ovh vão só o **título** e o **artista** da música (e a
chave, quando houver, só para o Vagalume). Nenhum áudio, nenhum dado seu ou dos convidados.

**Tempo:** quando o LRCLIB acha a letra, nada muda. Quando ele não acha, o preparo pode levar **alguns segundos a
mais** enquanto as outras fontes são consultadas (cada consulta desiste em ~5 s se não responder; no pior caso, cerca de 10 s por fonte).

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
| `S` | Liga/desliga a [pontuação e guia de notas](#pontuação-e-guia-de-notas) |
| `O` | Desloca a régua de notas: oitava original → 1 oitava abaixo → 1 oitava acima |
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
| `data/songs/` | áudio de cada música (instrumental e voz separados) e as notas da voz (`pitch.json`, usadas na pontuação) |
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
| A letra veio do Vagalume/lyrics.ovh, mas é de outra versão (ao vivo, remix) | Menu *Letra* → *Candidatas e colar LRC…*: escolha outra candidata ou *Buscar de novo* com outros termos. Se preferir só o LRCLIB e a voz, desligue *Buscar letra em outras fontes* na engrenagem |
| Nunca vem letra do Vagalume | Confira se *Buscar letra em outras fontes* está ligado. Sem chave, a app depende da página pública, que pode ter mudado: configure uma chave gratuita da API (veja [De onde vem a letra](#de-onde-vem-a-letra)) |
| Letra com palavras erradas | O chip diz "Extraída da voz (sem letra oficial)": menu *Letra* → *Colar letra…* e cole o texto oficial, sem tempos |
| Letra errada (outra música/versão) | Menu *Letra* → *Candidatas e colar LRC…* → trocar candidata ou *Buscar de novo* |
| Editor de sincronia sem forma de onda | Em músicas longas ou se o navegador não conseguir decodificar a voz, o editor mostra só a barra de tempo após alguns segundos. Todo o resto funciona |
| O navegador não pede o microfone / "Permita o microfone no navegador para pontuar" | Use o Palco em `http://127.0.0.1:8765/stage` (endereço seguro). Libere o microfone no ícone da barra de endereço e aperte `S` duas vezes. Confira se o sistema reconhece o microfone |
| A pontuação sobe sem ninguém cantar | O microfone está captando a voz guia da caixa. Afaste o microfone da caixa ou desligue a voz guia (`V`) |
| "Notas indisponíveis para esta faixa" | A app não conseguiu extrair as notas desta música. A letra e o som funcionam normalmente, só sem régua e placar |
| Régua engasgando numa máquina fraca | Desligue a pontuação com `S` no Palco |
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
  ligado. Com ele, pode demorar um pouco mais. Quando o LRCLIB não tem a letra, a consulta ao Vagalume e ao
  lyrics.ovh soma mais alguns segundos. A extração das notas para a pontuação soma menos de 1 s.
- **Pontuação: a voz guia que sai da caixa pode pontuar sozinha**, porque o microfone a capta. Deixe o microfone
  longe da caixa ou desligue a voz guia (`V`).
- Pontuação: no Palco em modo espelho (sem som), a régua pode ficar ~0,1 s fora do tempo.
- **Letras.mus.br não é suportado**: o site bloqueia acesso automático. Para músicas que só existem lá, use *Colar
  letra…* no menu *Letra*.
- Nas Configurações, se um ajuste falhar e você usar *Tentar de novo*, depois de salvar o foco do teclado vai para o
  fundo do modal (o botão some). Basta apertar `Tab` para voltar aos controles.
- Sem chave da API, a letra do Vagalume depende da página pública do site. Se o Vagalume mudar a página, essa fonte
  para de trazer letras (a app segue para o lyrics.ovh e a voz) até a app ser atualizada.
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
   No Windows, se você rodou `--configurar-rede`, desfaça antes: `karaoke.bat --desfazer-rede`. No Linux: `make firewall-undo`.
2. Faça backup de `data/` se quiser guardar a biblioteca.
3. Apague a pasta do projeto. Isso remove `.venv/`, os modelos e as músicas. Nada é instalado fora dela.
4. Opcional: o `pip` pode ter deixado downloads em cache em `~/.cache/pip`. Para limpar: `python3 -m pip cache purge`.

## Uso do conteúdo do YouTube

A app é para **uso pessoal e privado**, como cantar em casa com amigos. Os áudios baixados e separados ficam só no
seu computador. Não redistribua esse material e respeite os direitos autorais e os Termos de Serviço do YouTube.
As letras vêm do [LRCLIB](https://lrclib.net), um banco aberto de letras sincronizadas, e, quando ele não tem a
música, do [Vagalume](https://www.vagalume.com.br) e do [lyrics.ovh](https://lyrics.ovh). As letras pertencem aos
seus autores: use-as só para cantar em casa.

## Privacidade

Por padrão, o servidor escuta só em `127.0.0.1`, então nenhum outro aparelho da rede consegue acessá-lo. A rede
local só passa a ter acesso enquanto o [modo convidado](#modo-convidado-festa) estiver ligado, e mesmo assim só à
página do convidado e ao que ela usa (o Controle, as configurações e os áudios continuam só no PC). O microfone da
pontuação é analisado só no navegador do Palco: nada é gravado nem enviado. Nada sai da sua rede: os convidados conversam direto com o seu PC. A app só se conecta à internet para falar com o YouTube (busca e
download), as fontes de letra (LRCLIB e, se ligadas, Vagalume e lyrics.ovh, recebendo só título e artista) e o Hugging Face (download dos modelos no 1º uso). Não há telemetria.
