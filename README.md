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

1. **Buscar**: na aba *Buscar* do Controle, digite artista ou música e aperte Enter. Atalho: `/` foca a busca.
2. **Preparar e enfileirar**: no resultado, clique em *Preparar e enfileirar* e informe quem vai cantar. A música aparece
   em *Processamento* (Baixando, Separando voz, Buscando letra) e entra na fila quando ficar pronta.
   *Só preparar* deixa a música pronta na Biblioteca sem colocá-la na fila.
3. **Cantar**: o Palco toca a fila na ordem. Com o auto-avanço ligado, a próxima começa após uma contagem "A seguir".
   Ajuste o volume e a **voz guia** (o quanto da voz original fica audível) no Controle ou pelos atalhos do Palco.
4. **Letra errada ou fora do tempo?** Na Biblioteca, abra o menu *Letra* da música, ou aperte `E` com ela tocando.
   Veja [Letras e sincronia](#letras-e-sincronia).
5. Reordene a fila arrastando pela alça, com os botões ↑/↓ ou com `Alt+↑/↓`. `Del` remove (com *Desfazer*).

As músicas preparadas ficam na **Biblioteca** e tocam na hora, sem internet, nas próximas vezes.

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

O servidor escuta só em `127.0.0.1`, então nenhum outro aparelho da rede consegue acessá-lo. A app só se conecta
à internet para falar com o YouTube (busca e download), o LRCLIB (letras) e o Hugging Face (download dos modelos
no 1º uso). Não há telemetria.

## Futuro: modo convidado (QR code)

Uma próxima versão deve permitir que os convidados escaneiem um QR code no Palco e adicionem músicas à fila pelo
celular. Isso exige abrir o acesso pela rede local, e a arquitetura já está preparada para isso
(`docs/adr/0002-guest-mode-readiness.md`). Nesta versão (0.2.0) o acesso pela rede fica **desligado de propósito**:
`KARAOKE_HOST=0.0.0.0` é recusado.
