# DESIGN — Karaokê local

> Autor: ux-designer · Ciclo: `20261003-2339-karaoke` · Protótipo: `design/prototype.html` · Tokens: `design/tokens.css`
> Histórias cobertas: US-01 … US-07 · Restrições do architect incorporadas (DECISION pós-T-001).
> **v0.2** (ciclo `20261004-1742-karaoke`, T-002): US-08 e US-09 — ver **§11 Letras e sincronia**.

Como abrir o protótipo: abra `design/prototype.html` direto no navegador (file://, sem rede). A barra preta no topo
**não faz parte do produto**; ela alterna telas e estados. Também aceita parâmetros, úteis em revisão/screenshot:
`?scene=play|paused|loading|upnext|upnext-manual|prep|mirror|nolyrics|error|start|idle`, `&t=12` (posição em s),
`&sync=line|word`, `&help=1`, `&theme=light`, `&search=empty|loading|error|ok`, `&data=none`, `&tab=lib`,
`&modal=lyrics|enqueue|long|delete|clear|settings`, `&warn=stage,cpu`, `&still=1` (desliga animações).
v0.2: `&editor=1` abre o editor de sincronia; `&estate=loading|empty|saving|error|dirty` força um estado;
`&emode=mark` abre no modo "Tocar e marcar"; `&menu=prep` abre o menu do botão split; `&lyricsmenu=1` abre o menu Letra da biblioteca.

---

## 1. Princípios
1. **Legível a 3 metros.** No Palco a letra é a única protagonista: enorme, alto contraste, nada competindo com ela.
2. **Sempre dizer o que está acontecendo.** Toda espera tem etapa, progresso ou contagem; todo erro diz o que fazer.
3. **Festa sem poluição.** A cor vem de luz (gradiente, brilho, arte desfocada), não de enfeites. Uma ação primária por tela.

## 2. Direção visual
- **Tom:** "palco à noite". Fundo índigo quase preto, luz magenta (marca/ação) e ciano (luz de palco/progresso/letra cantada).
- **Paleta (tema escuro, padrão):**

  | Papel | Token | Hex |
  |---|---|---|
  | Fundo | `--color-bg` | `#0e0b1a` |
  | Superfície / elevada / hover | `--color-surface` / `-2` / `-3` | `#17132a` / `#211b3a` / `#2a2348` |
  | Borda decorativa / de componente | `--color-border` / `--color-border-strong` | `#342c55` / `#6e6496` |
  | Texto / secundário / terciário | `--color-text` / `-muted` / `-subtle` | `#f4f1ff` / `#b3abd1` / `#8f87b0` |
  | Primária (ação, marca) | `--color-accent` (+ `--color-on-accent`) | `#ff4d94` (texto `#1a0612`) |
  | Informação / foco / progresso | `--color-info`, `--color-focus` | `#4fddff` |
  | Sucesso · Aviso · Erro | `--color-success` · `--color-warning` · `--color-danger` | `#3ddc97` · `#ffc24b` · `#ff6b7a` |
  | Gradiente de marca | `--gradient-brand` | `#ff4d94 → #b05cff → #4fddff` |

  Tema claro (só Controle): fundo `#f7f5fc`, superfícies `#ffffff`/`#efebf8`, texto `#1a1530`/`#5a5378`,
  primária `#c2185b` com texto branco, info `#00739e`, sucesso `#13784a`, aviso `#8f5b00`, erro `#c42536`, foco `#0067a3`.
  Tema: segue `prefers-color-scheme`, sobrescrito por `<html data-theme="light|dark">` (botão sol/lua no header, persistir em settings/localStorage).
  **Palco é sempre escuro** (não aplica `data-theme`).
- **Tipografia:** uma família, a **fonte de sistema** (`--font-sans`: system-ui, Cantarell, Noto Sans, Roboto…). Zero download,
  zero rede, não há woff2 a vendorizar. `--font-mono` só para LRC e teclas. Números com `font-variant-numeric: tabular-nums` (`.tnum`).
  Escala Controle 12/14/16/18/22/28/36 px. Escala Palco em `vh` com `clamp()` (`--stage-*`): linha atual 7,5 vh (≥ 5 vh do AC-06.1),
  próxima 4,6 vh, título 4 vh, meta 2,6 vh, contagem 18 vh.
  *Se o frontend quiser uma fonte display no Palco, usar uma livre (OFL) em woff2 em `static/fonts/` — opcional, não exigido.*
- **Forma:** raios 6/10/16/24 px e pílula; sombras suaves e profundas no escuro; brilho magenta só no botão Play (`--glow-accent`).
  Densidade média; alvos ≥ 40 px (`--control-h`).
- **Movimento:** 80 ms (press), 150 ms (hover/foco), 220 ms (troca de estado/toast/modal), 400 ms (troca de linha no Palco),
  700 ms (troca de cena no Palco). Easing `--ease-out` para entrar, `--ease-in-out` para cenas. Com `prefers-reduced-motion`
  todos os tokens de duração viram 0 e as animações decorativas (holofotes, pulso, equalizador, skeleton) param.
- **Ícones:** set próprio em SVG inline (traço 2 px, grade 24, `stroke: currentColor`) — copiar os `<symbol>` do protótipo para
  um sprite `static/icons.svg` (CSP `'self'` permite `<use href="/static/icons.svg#i-play">`). Nada de emoji.

## 3. Mapa de telas
```
Controle  /                                         Palco  /stage  (TV, tela cheia)
┌─────────────────────────────────────┐            ┌───────────────────────────────────────────┐
│ Header: logo · busca (/) · Abrir     │  "Abrir    │ [Pressione Enter] ─Enter─► (claim do áudio) │
│ Palco · tema                         │  Palco" ─► │        │                                   │
│ [Banners: Palco desconectado / CPU]  │            │        ▼                                   │
│ Abas: Buscar | Biblioteca  │ Proces- │            │ fila vazia? ─sim─► [Espera]                 │
│                            │ samento │            │        │não                                │
│                            │ Fila    │            │ [Carregando] ─► [Tocando ⇄ Pausado]         │
│ Mini-player (sticky)                 │            │   ↳ intervalo >5 s: [Indicador de espera]   │
└─────────────────────────────────────┘            │   ↳ sem letra: [Letra indisponível]         │
Modais: Enfileirar(cantor) · Vídeo longo ·          │ fim ─► [A seguir (contagem | manual)] ─►    │
Gerir letra · Excluir · Limpar fila                 │        próxima pronta? não ─► [Aguardando   │
                                                    │        preparo] / fim da fila ─► [Espera]    │
                                                    │ Outro Palco tem o áudio ─► [Modo espelho]    │
                                                    │ Falha ao tocar ─► [Erro] ─5 s─► próxima      │
                                                    │ Sobreposições: HUD (teclas) · Atalhos (?)    │
                                                    └───────────────────────────────────────────┘
```
Mapeamento para `PlayerState.status` (contrato): `waiting_gesture`→Pressione Enter · `idle`/`ended`→Espera ·
`loading`→Carregando · `playing`/`paused`→Tocando/Pausado · `up_next`→A seguir (`starts_at=null` → variante manual) ·
`stage.audio_owner ≠ meu client_id`→Modo espelho · `song.lyrics` vazio→Letra indisponível.

## 4. Fluxos (caminho feliz)
### US-01 Buscar
1. Controle abre com a aba **Buscar** vazia (estado vazio com sugestões). `/` foca a busca de qualquer lugar.
2. Digita "queen" + Enter → skeleton (5 linhas) → até 15 resultados.
3. Resultado já na biblioteca: chip **Pronta** e primária **Adicionar à fila**. Em processamento: chip "Processando 62%",
   ação secundária "Adicionar à fila". Live: chip **Ao vivo**, botões desabilitados + explicação. > 15 min: chip **Vídeo longo**.
### US-02 Processar
1. Em um resultado novo, primária **Preparar e enfileirar** (abre modal "Quem vai cantar?") ou secundária **Só preparar** (toast).
2. Vídeo > 15 min → modal de confirmação ("Preparar mesmo assim" / "Escolher outro").
3. O job aparece em **Processamento**: barra segmentada (Baixando · Separando voz · Buscando letra) + rótulo da etapa + % e ETA.
   Primeiro uso: item "Modelos de IA (~2 GB) — só no primeiro uso" com barra **indeterminada** quando `stage_progress=null`.
4. Ao terminar: job some do painel, toast "Pronta", item entra na biblioteca; na fila o chip de status some.
5. Erro: segmento vermelho, motivo legível (`error.message`), **Tentar de novo** (primária) e Descartar.
### US-03 Letra
1. Biblioteca › ícone **Letra** (ou mini-player) → modal "Letra — <música>" com abas **Candidatas** · **Colar LRC** · **Gerar com Whisper**.
2. Candidatas mostram origem, diferença de duração (chip de aviso se ≠) e prévia; a em uso tem chip "Em uso".
3. Colar LRC valida ao digitar: erro diz a linha e mostra o formato esperado.
4. **Ajuste de sincronia** (−/+ 50 ms, Zerar) no mesmo modal e no mini-player; no Palco `[` adianta e `]` atrasa.
### US-04 Biblioteca
Aba **Biblioteca** (contador na aba) → filtro local → linha com thumb, título, artista · duração · offset, chip de origem
(LRCLIB verde / Whisper ciano / Manual magenta), ações **Fila**, **Letra**, **Excluir** (modal de confirmação com tamanho em disco).
### US-05 Fila
1. Painel **Fila**: seção "Tocando agora" (destacada) + "Próximas" numeradas.
2. Reordenar: arrastar pela alça, botões ↑/↓ sempre visíveis, ou foco no item + `Alt+↑/↓`; `Del` remove (toast com **Desfazer**).
3. Itens ainda processando mostram chip com etapa e % ("será pulada até ficar pronta" no title).
4. **Limpar** pede confirmação (não remove a que está tocando).
### US-06 Cantar
1. Controle › **Abrir Palco** → nova janela `/stage` → arrastar para a TV → `F` (tela cheia).
2. Palco mostra **Pressione Enter** → Enter → claim do áudio → **Carregando** → **Tocando**.
3. Intro/intervalos > 5 s: três pontos que acendem na contagem final (3-2-1) + "Prepare-se…" / "Solo instrumental · volta em N s",
   com a próxima linha já visível.
4. Fim: **A seguir** com arte, título, artista, "Bruno, é sua vez!" e anel de contagem (8 s). Auto-avanço desligado: sem anel,
   "Enter quando estiver pronto".
### US-07 Controles
Mini-player no Controle (anterior · play/pause · próxima · auto-avanço; seek; volume; **Voz guia**; offset) e teclado no Palco
(seção 7). Toda tecla no Palco mostra o **HUD** por 1,5 s.

## 5. Telas
### 5.1 Controle (`/`) — desktop ≥ 1100 px
```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ [◉ Karaokê]        [🔍 Buscar música no YouTube (artista, título)…        / ]  [▭ Abrir Palco] [☀] │  header 64px, sticky
├──────────────────────────────────────────────────────────────────────────────────────┤
│ ⚠ Palco desconectado. A música foi pausada. Abra o Palco na TV…        [Abrir Palco]  │  banners (só quando houver)
│ ⓘ GPU indisponível — usando CPU. Tudo funciona, mas…                       [Entendi]  │
├───────────────────────────────────────────────────────┬──────────────────────────────┤
│ [Buscar] [Biblioteca 6]                               │ ⚙ Processamento 4      [GPU] │
│ 6 resultados para "queen"                             │ ▣ Luzes de Neon              │
│ ▣3:31 Título (2 linhas máx., title= completo)         │   ███ ██▒░ ░░░               │
│       Canal · 1,2 mi visualizações  [✓ Pronta]        │   Separando voz · 2 de 3  62%│
│                               [+ Adicionar à fila]    │ ▣ Noite Sem Fim              │
│ ▣3:58 Título…                                         │   ▒▒▒ (vermelho)             │
│       Canal · …       [Só preparar] [🎤 Preparar e    │   ⚠ Download falhou: …       │
│                                      enfileirar]      │   [↻ Tentar de novo][Descartar]│
│ ▣1:12:40 Coletânea…  [⚠ Vídeo longo]   …              │ ▣ Modelos de IA (~2 GB) ⟿     │
│ ▣AO VIVO Rádio…      [Ao vivo]  (desabilitado+motivo) ├──────────────────────────────┤
│                                                       │ ☰ Fila 4 músicas    [Limpar] │
│                                                       │ TOCANDO AGORA                │
│                                                       │ ▶ ▣ Canção da Estrada · Ana  │
│                                                       │ PRÓXIMAS                     │
│                                                       │ 2 ⠿ ▣ Luzes de Neon · Bruno  │
│                                                       │       [⏱ Separando voz 62%] ↑↓✕│
│                                                       │ 3 ⠿ ▣ Dança no Telhado    ↑↓✕│
│                                                       │ Arraste… Alt+↑/↓ · Del       │
├───────────────────────────────────────────────────────┴──────────────────────────────┤
│ ▣ Canção da Estrada Vazia   |  ⏮  (⏸)  ⏭  [↻]         | 🔊 Volume  ━━━━━━●━  80%     │  mini-player sticky
│   Os Faróis · cantando: Ana |  1:12 ━━━━━●━━━━━━ 3:31  | 🎤 Voz guia ●━━━━━━━  0%     │
│                             |                          | ≡ Letra   [− +120 ms +]      │
└──────────────────────────────────────────────────────────────────────────────────────┘
```
- **Ação primária:** "Preparar e enfileirar" (ou "Adicionar à fila" quando Pronta). Magenta sólido; demais ações são
  secundárias (contorno) ou ghost (texto). Excluir/Limpar usam vermelho.
- Grade: conteúdo `minmax(0,1fr)` + coluna lateral 380 px, gutter 24 px, largura máx. 1440 px.

**Estados**
| Região | Vazio | Carregando | Erro | Sucesso |
|---|---|---|---|---|
| Busca | Ícone + "O que vamos cantar hoje?" + dica `/` + 3 sugestões (botões que preenchem a busca) | 5 linhas skeleton (thumb + 2 barras + botão), `aria-busy` | Ícone wifi cortado + "Sem conexão com o YouTube" + explicação + **Tentar de novo** + "Abrir Biblioteca" (offline) | Lista de resultados com chips |
| Biblioteca | "Sua biblioteca está vazia" + **Buscar a primeira música** | (instantâneo, local) skeleton se > 300 ms | Toast de erro com motivo | Lista + filtro; filtro sem resultado: "Nada encontrado para 'x'" + limpar filtro |
| Processamento | "Nada sendo preparado" + explica as etapas | barra por etapa (determinada ou indeterminada) | item vermelho + motivo + Tentar de novo / Descartar | item some + toast "Pronta" |
| Fila | "Fila vazia" + como adicionar | — | toast se reordenação falhar (volta à ordem do servidor) | lista |
| Mini-player | "Nada tocando · Adicione músicas à fila" com controles desabilitados | play com spinner enquanto `loading` | `409 stage_not_connected` → banner Palco desconectado + "Abrir Palco" | controles ativos |
| Thumbnail | — | fundo `--color-surface-3` | proxy falhou → placeholder (ícone nota musical sobre `--color-surface-3`, `title="Miniatura indisponível"`) | imagem 16:9 `object-fit: cover` |

**Avisos globais (banners abaixo do header, empilháveis)**
- *Palco desconectado* (aviso, amarelo): quando `stage.connected=0` ou o owner caiu; ação **Abrir Palco**.
- *GPU indisponível — usando CPU* (info, ciano): de `system.gpu_warning`; ação **Entendi** (dispensa na sessão). Chip do
  painel Processamento troca **GPU** (ciano) por **CPU** (amarelo).
- *Live/stream*: inline no resultado (não é banner). *Vídeo longo*: modal de confirmação.

### 5.2 Controle — responsivo
- **≥ 1280:** layout acima. **1100–1280:** coluna lateral mantém 380 px, resultados encolhem.
- **720–1100:** coluna única (busca/biblioteca → processamento → fila); mini-player em 2 linhas (transporte embaixo).
- **360–720:** header sem texto nos botões (só ícones com `aria-label`); resultados com ações em linha própria;
  mini-player deixa de ser sticky e empilha (agora tocando · mixer · transporte). Sem scroll horizontal.

### 5.3 Palco (`/stage`) — 16:9, tela cheia, sempre escuro
Fundo comum: thumbnail da música atual **desfocada 48 px, saturada 1,3, escala 1,1** + overlay `rgb(7 5 15 / .78)` + vinheta radial.
Sem música: holofotes animados (magenta, ciano, violeta) sobre `--stage-bg`.

**Tocando**
```
┌──────────────────────────────────────────────────────────────────────────┐
│ Canção da Estrada Vazia                                       A SEGUIR    │  meta 2,6vh
│ Os Faróis                                                Luzes de Neon    │
│ (👤 Ana)                                                        Bruno     │
│                                                                           │
│                                                                           │
│           Saí de casa sem olhar pra trás          ← 7,5vh, peso 800        │
│           ^^^^^^^^^^^^^^^^ (ciano = cantado, branco = a cantar)           │
│               A estrada inteira só pra mim        ← 4,6vh, lilás          │
│                                                                           │
│                         [⏸ Pausado · Espaço continua]  (só se pausado)    │
│ 0:08 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0:42 │  barra 0,8vh gradiente
└──────────────────────────────────────────────────────────────────────────┘
```
| Estado | Conteúdo |
|---|---|
| **Pressione Enter** | Logo, "Pressione **Enter** para começar" (pulso lento), "3 músicas na fila · a primeira é X", nota "O navegador só libera o som depois de uma tecla". |
| **Espera (fila vazia / fim)** | Logo, "Quem canta primeiro?", instrução "No PC, abra o Controle, busque uma música e clique em **Preparar e enfileirar**", endereço `http://127.0.0.1:<porta>`, dica `?`. Holofotes. |
| **Carregando** | Equalizador animado (5 barras gradiente), "Carregando 'X'…", "<cantor>, prepare o microfone". Máx. ~2 s normalmente. |
| **Tocando** | Wireframe acima. |
| **Pausado** | Igual a Tocando com letra a 50 % de opacidade, barra em cinza e pílula "⏸ Pausado · Espaço continua" acima da barra. |
| **Intervalo > 5 s / intro** | Três pontos de 4 vh no lugar da linha atual; legenda "Prepare-se…" (intro) / "Solo instrumental · volta em N s"; nos últimos 3 s os pontos acendem em ciano um a um (contagem 3-2-1) e a legenda vira "Volta em N"/"Começa em N". Próxima linha já visível embaixo. |
| **Letra indisponível** | Topo e barra normais; no centro ícone de letra + "Letra indisponível" + "Curta o instrumental — ou, no Controle, abra Letra › Colar LRC ou Gerar com Whisper". |
| **A seguir (auto)** | Arte 16:9 grande, "A SEGUIR" magenta, título 7,5 vh, artista, pílula "<cantor>, é sua vez!", anel de contagem magenta (8 s, `starts_at`). Dicas: Enter começar agora · A pausar auto-avanço · N pular. |
| **A seguir (manual)** | `starts_at=null`: sem anel. Dica principal em branco: "Enter quando estiver pronto" · A ligar auto-avanço · N pular. |
| **Aguardando preparo** | Próxima ainda processando: "QUASE LÁ", "Preparando 'X'", etapa atual, barra + "% · cerca de N s", dica N pular para a próxima pronta. |
| **Modo espelho** | Igual a Tocando (letra sincronizada pelo `player.position` do servidor), com pílula ciano persistente "▭ Modo espelho — o som sai no outro Palco · Enter tocar aqui". Enter = `POST /api/stage/claim` (se o architect não permitir roubar o áudio, trocar o texto por "Feche o outro Palco para tocar aqui"). |
| **Erro ao tocar** | Ícone de alerta vermelho, "Não deu para tocar esta música", motivo, "Pulando para a próxima em 5 s…", Enter pular agora · Espaço esperar aqui. |

**Sobreposições**
- **HUD** (topo central, pílula escura translúcida, 1,5 s): ícone + rótulo + barra (quando há valor 0–100) + valor.
  Ex.: "🔊 Volume ━━━━● 80 %", "🎤 Voz guia 30 %", "≡ Letra +150 ms", "Auto-avanço desligado", "⏭ Próxima".
- **Atalhos (`?`)**: tela cheia `rgb(7 5 15 / .96)`, título "Atalhos do Palco", grade de 2 colunas tecla → ação, rodapé "Esc fecha".

## 6. Letra sincronizada — regras de renderização
- **Relógio:** `t = audioClock − offset_ms/1000`, onde `audioClock` vem do `AudioContext` (owner) ou de
  `position_ms + (now − position_at)` (espelho). Nunca `setInterval`; atualizar em `requestAnimationFrame`.
  Convenção do offset: **`]` atrasa a letra (+50 ms), `[` adianta (−50 ms)**; offset positivo = letra aparece mais tarde.
- **Linha atual / próxima:** linha atual = a primeira com `t < fim`. Mostrar sempre a atual (grande) e a seguinte (menor).
  Troca de linha: a nova entra com `translateY(3vh)→0` + fade em 400 ms (0 ms com reduced-motion).
- **Preenchimento:** cada palavra é um `<span>` com `--p` (0..1) e
  `background: linear-gradient(90deg, var(--stage-text-sung) calc(var(--p)*100%), var(--stage-text) 0); background-clip: text; color: transparent`.
  Sombra via `filter: drop-shadow` **no contêiner** (não `text-shadow`, que borra o clip).
  - **Whisper (tempo por palavra):** `--p = (t − w.start)/(w.end − w.start)` por palavra.
  - **LRCLIB (só tempo por linha):** fim estimado `= min(início_da_próxima − 0,2 s, início + 0,45 s × nº_palavras + 0,8 s)`;
    distribuir esse intervalo entre as palavras **proporcionalmente ao nº de caracteres** (inclui o espaço) → preenchimento
    contínuo e uniforme. É o que o protótipo faz no modo "Por linha (LRCLIB)" (botão na barra do protótipo).
- **Intervalo:** se `início_da_próxima − fim_da_anterior > 5 s` e `t` está nesse vão → estado "Intervalo" (seção 5.3).
  O mesmo vale para a intro (antes da 1ª linha, se > 5 s).
- **Linhas longas:** quebra natural, `max-width: 92vw`, centralizado; nunca reduzir abaixo de `--stage-line-current` para caber
  (aceitar 2 linhas visuais).

## 7. Acessibilidade
### Contraste verificado (WCAG 2.x, calculado)
| Par (tema escuro) | Razão | | Par (tema claro) | Razão |
|---|---|---|---|---|
| text `#f4f1ff` / bg | 17,45 | | text `#1a1530` / bg `#f7f5fc` | 16,27 |
| text / surface `#17132a` | 16,23 | | text / surface `#fff` | 17,59 |
| text / surface-2 `#211b3a` | 14,72 | | muted `#5a5378` / bg | 6,60 |
| text / surface-3 `#2a2348` | 13,18 | | muted / surface-2 `#efebf8` | 6,08 |
| muted `#b3abd1` / surface | 8,31 | | muted / surface-3 `#e5dff3` | 5,50 |
| muted / surface-2 | 7,53 | | subtle `#6f6890` / surface | 5,17 |
| muted / surface-3 | 6,75 | | branco / accent `#c2185b` | 5,87 |
| subtle `#8f87b0` / surface | 5,38 | | branco / accent-hover `#a8124d` | 7,35 |
| on-accent `#1a0612` / accent `#ff4d94` | 6,25 | | accent / surface | 5,87 |
| on-accent / accent-hover `#ff6ba7` | 7,34 | | accent / accent-soft | 4,98 |
| accent / surface | 5,80 | | info `#00739e` / surface | 5,32 · soft 4,63 |
| accent / accent-soft (chip) | 4,89 | | success `#13784a` / surface | 5,51 · soft 4,80 |
| info `#4fddff` / surface | 11,28 · soft 8,42 | | warning `#8f5b00` / surface | 5,73 · soft 5,00 |
| success `#3ddc97` / surface | 10,22 · soft 7,84 | | danger `#c42536` / surface | 5,73 · soft 4,88 |
| warning `#ffc24b` / surface | 11,24 · soft 8,36 | | branco / danger | 5,73 |
| danger `#ff6b7a` / surface | 6,57 · soft 5,42 | | border-strong `#8c84a8` / surface (UI ≥ 3:1) | 3,51 |
| on-danger `#1f0508` / danger | 7,05 | | foco `#0067a3` / bg · surface | 5,60 · 6,05 |
| border-strong `#6e6496` / surface (UI ≥ 3:1) | 3,38 | | | |
| foco `#4fddff` / bg | 12,13 | | | |

**Palco — texto sobre thumbnail desfocada.** Overlay `rgb(7 5 15 / .78)` sobre o **pior caso** (thumb branca pura → fundo
efetivo `#3e3c44`): letra branca **10,85**, letra cantada ciano **6,78**, próxima linha `#cfc8ea` **6,77**, meta `#b9b1d9`
**5,35**. Sobre thumb amarela saturada: branco 9,1, ciano 5,7. Sobre fundo puro `#07050f`: 20,2 / 12,6 / 12,6 / 10,0.
Magenta `#ff4d94` no pior caso dá 3,48 → usado só em texto grande (≥ 3 vh, "A SEGUIR") e decorativo. Reforço extra: vinheta +
`drop-shadow` na letra. *Se trocar o overlay, recalcular.*

### Atalhos de teclado
**Palco** (todas viram `POST /api/player/command`, exceto as marcadas como locais):
| Tecla | Ação | `action` |
|---|---|---|
| `Enter` | Começar (gesto/claim) · começar já no "A seguir" · pular agora no erro · tocar aqui no espelho | `POST /api/stage/claim` / `play` / `next` |
| `Espaço` | Tocar/pausar | `toggle` |
| `←` / `→` | Voltar / avançar 5 s | `seek_relative ∓5000` |
| `↑` / `↓` | Volume ±5 % | `volume_relative ±0.05` |
| `N` | Próxima música | `next` |
| `P` | Reiniciar (se > 3 s) / anterior | `prev` |
| `−` / `+` (`=`) | Voz guia ∓10 % | `guide_vocal_relative ∓0.1` |
| `V` | Voz guia liga/desliga (alterna 0 ↔ último valor) | `guide_vocal` |
| `[` / `]` | Letra adianta / atrasa 50 ms | `offset_relative −50 / +50` |
| `A` | Auto-avanço liga/desliga | `autoplay` |
| `F` | Tela cheia (local) | — |
| `?` | Mostrar/ocultar atalhos (local) | — |
| `Esc` | Fecha atalhos; senão sai da tela cheia (local) | — |
Controles de TV/remoto comuns mapeiam para setas/Enter/Espaço — suficientes para tudo que importa.

**Controle:** `/` foca a busca · `Enter` busca · `Esc` fecha modal/limpa busca focada · na fila: `Alt+↑/↓` move, `Del` remove ·
Tab navega em ordem visual (header → banners → abas → lista → painel lateral → mini-player). Modais prendem o foco, Esc fecha,
foco volta ao elemento que abriu.

### Outros
- Foco visível: anel duplo `--focus-ring` (2 px fundo + 2 px ciano/azul) em tudo que é interativo.
- Inputs sempre com `<label>` (visível ou `.sr-only`); botões só-ícone com `aria-label` + `title`.
- Regiões `aria-live="polite"`: resultados, processamento, toasts, HUD do Palco (`role="status"`).
- Abas com `role="tablist/tab/tabpanel"` e `aria-selected`; fila com `aria-label` por item incluindo cantor e status.
- Nada essencial só em hover: ↑/↓/✕ da fila ficam sempre visíveis.

## 8. Componentes
| Componente | Variações | Estados | Notas |
|---|---|---|---|
| Button | primary, secondary, ghost, danger, danger-ghost, icon, lg, round | hover, focus-visible, active (scale .97), disabled (45 %), loading (ícone gira) | altura 40 px (48 lg); verbo no rótulo |
| Botão Play | redondo 44 px magenta + glow | idem; ícone alterna play/pause | um por tela |
| Toggle (auto-avanço) | botão ícone com `aria-pressed` | pressed = magenta suave | também `switch` CSS disponível |
| Input / Search | texto, busca com ícone e `kbd /` | hover, focus (borda ciano), invalid (borda vermelha + hint) | |
| Textarea LRC | mono | idem + mensagem de erro com linha | |
| Range | volume (magenta), voz guia (ciano) | hover, focus, disabled | trilha preenchida via `--val` |
| Offset stepper | − valor + | hover, focus | valor `+120 ms` tabular |
| Chip | success (Pronta, LRCLIB), info (Processando, Whisper, GPU), warning (Vídeo longo, CPU, duração difere), danger (Ao vivo), accent (Manual), neutral (contador) | — | 24 px, ícone 14 px opcional |
| Tabs | sublinhado magenta 3 px | hover, selected, focus | |
| Result row | normal, pronta, processando, longo, live, sem thumb | hover (surface-2) | título 2 linhas + `title` |
| Job item | etapa determinada, indeterminada, aguardando, erro | — | barra segmentada em 3 (download de modelo = 1 segmento) |
| Queue item | tocando agora, normal, processando | hover/focus-within, dragging (50 %), drop-target (linha magenta) | `draggable`, alça ⠿ |
| Library row | com/sem offset; origem LRCLIB/Whisper/Manual | hover | |
| Modal | padrão 560 px, largo 720 px | abrir (fade + pop 220 ms) | trap de foco, Esc, clique fora fecha |
| Toast | sucesso, erro; com Desfazer | entra pop 220 ms, sai após 5 s | canto inferior direito acima do player |
| Banner | warning, info | — | ação à direita |
| Empty state | normal, compacto (painéis laterais) | — | ícone em círculo + título + frase + ação |
| Skeleton | linhas de resultado | shimmer 1,2 s (parado com reduced-motion) | |
| Stage: linha de letra | atual, próxima, entrando | — | ver seção 6 |
| Stage: pílulas | pausado, espelho, cantor | — | |
| Stage: HUD | com barra / só valor | entra 220 ms, sai após 1,5 s | |

## 9. Notas para o frontend-engineer
- `design/tokens.css` é a fonte; copiar para `static/css/tokens.css` (sem editar valores; pedir CR ao ux-designer).
- **CSP `script-src 'self'`:** o protótipo usa JS/handlers inline só por ser protótipo. Na produção: nada de `onclick=""`,
  nada de `<script>` inline, nada de `style=""` dinâmico via atributo se a CSP de estilo for restrita — preferir
  `element.style.setProperty('--p', v)` (permitido pela CSSOM) e classes.
- As cores da arte fictícia (`--art1..6`) existem só no protótipo; na produção o fundo do Palco é a thumbnail real
  (`/api/songs/<id>/thumb`) em `background-image`, com o mesmo blur/overlay.
- Pré-carregar a thumb do "A seguir" durante `up_next` para a troca de fundo não piscar (crossfade 700 ms).
- Não desenhar o HUD/pílulas sobre a linha atual: HUD fica no topo central, pílulas logo acima da barra de progresso.

## 10. Responsividade (Palco)
Toda a escala do Palco é em `vh` com `clamp()`, então 720p, 1080p e 4K mantêm a mesma composição. Em telas muito largas
(21:9) a letra respeita `max-width: 92vw`. Abaixo de 600 px de altura (janela pequena no PC) os mínimos do `clamp()` garantem
leitura (linha atual ≥ 40 px).

---

## 11. v0.2 — Letras e sincronia (US-08, US-09)

> Ciclo `20261004-1742-karaoke` · T-002. Problema a resolver: o ajuste atual (offset ±50 ms no modal/mini-player e `[`/`]` no
> Palco) só corrige atraso **constante** e obriga a ir e voltar entre PC e TV. A v0.2 troca isso por: (1) letra já sincronizada
> pela voz por padrão, (2) ações automáticas em um clique e (3) um **editor de sincronia** com prévia própria no Controle.

### 11.1 Princípios desta área
1. **Automático primeiro, manual por último.** Toda tela de letra oferece antes a correção automática ("Sincronizar pela voz",
   "Detectar automaticamente"); a edição manual é o plano B e nunca o caminho obrigatório.
2. **Ouvir e ver no mesmo lugar.** O editor toca a música **no próprio Controle** (forma de onda da voz + linha atual destacada);
   ninguém precisa olhar a TV para acertar o tempo.
3. **Nada se perde.** Cada resultado vira uma nova candidata; salvar no editor cria/atualiza a "Manual" sem apagar a original;
   desfazer/refazer ilimitado dentro da sessão.

### 11.2 Modos de letra — Configurações (AC-08.1)
Novo botão **engrenagem** no header (entre "Abrir Palco" e tema; `aria-label="Configurações"`) abre o modal **Configurações**
(560 px). Primeira e, por ora, única seção: **Letras**.

```
┌ Configurações ───────────────────────────────────────── ✕ ┐
│ LETRAS                                                     │
│ Como obter a letra das próximas músicas                    │
│ ┌────────────────────────────────────────────────────────┐ │
│ │ (●) Online + sincronizada pela voz   [Recomendado]      │ │  radio-card selecionado: borda magenta + accent-soft
│ │     Busca a letra no LRCLIB e acerta o tempo de cada    │ │
│ │     linha e palavra ouvindo a voz. Melhor texto e       │ │
│ │     melhor tempo. Sem letra online, extrai da voz.      │ │
│ │     ⏱ +~20 s por música na GPU                           │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ ( ) Extrair da voz                                       │ │
│ │     Transcreve a letra ouvindo a voz (Whisper). Para    │ │
│ │     músicas sem letra online. Pode errar palavras.      │ │
│ ├────────────────────────────────────────────────────────┤ │
│ │ ( ) Online como está                                     │ │
│ │     Usa a letra e os tempos do LRCLIB, sem ajuste.      │ │
│ │     Mais rápido, mas às vezes fica fora do tempo.       │ │
│ └────────────────────────────────────────────────────────┘ │
│ ⓘ Vale para músicas novas. Para uma música pronta, use     │
│   Biblioteca › Letra › Sincronizar pela voz.               │
│                                    ✓ Salvo        [Fechar] │
└────────────────────────────────────────────────────────────┘
```
- `role="radiogroup"` com `aria-labelledby` no título; setas ↑/↓ trocam a opção (padrão nativo de radio). Cada card é um
  `<label>` inteiro clicável (≥ 72 px de altura).
- **Salva ao escolher** (`PATCH /api/settings {lyrics_mode}`), sem botão "Salvar": aparece "✓ Salvo" (texto `--color-success`,
  220 ms fade) ao lado de **Fechar** (primária única). Falha → "Não deu para salvar. [Tentar de novo]" em `--color-danger`
  e o radio volta ao valor do servidor.
- Valores: `aligned` → "Online + sincronizada pela voz" · `transcribe` → "Extrair da voz" · `online` → "Online como está".

### 11.3 Override por música no Preparar (AC-08.2)
O caminho rápido não muda: **Preparar e enfileirar** continua sendo um clique. O override mora num **botão split**:

```
[Só preparar]  [🎤 Preparar e enfileirar │ ▾]
                                         └─ menu (role="menu", 300 px, alinhado à direita)
                                            LETRA DESTA MÚSICA
                                            ✓ Padrão: Online + voz          ← menuitemradio, aria-checked
                                              Online + sincronizada pela voz
                                              Extrair da voz
                                              Online como está
                                            ───────────────────
                                            Vale para "Preparar e enfileirar"
                                            e "Só preparar" deste resultado.
```
- O segmento ▾ é um botão próprio (40×40, mesma cor da primária, separador 1 px `--color-on-accent` a 25 %),
  `aria-label="Opções de letra para esta música"`, `aria-haspopup="menu"`, `aria-expanded`. Abre com clique, `Enter`/`Espaço`
  ou `Alt+↓` quando o foco está na primária. Dentro: ↑/↓ movem, `Enter` escolhe e fecha, `Esc` fecha e devolve o foco ao ▾,
  `Tab` fecha. Clique fora fecha.
- Escolher um modo diferente do padrão **não dispara** o preparo; marca o resultado com o chip removível
  **`Letra: Extrair da voz ✕`** (chip neutro com ícone de letra, ao lado do título/meta). Os dois botões do resultado passam a
  usar esse modo (`POST /api/jobs {…, lyrics_mode}`). `✕` do chip = voltar ao padrão.
- No modal **Adicionar à fila** (Quem vai cantar?) aparece uma linha discreta abaixo do campo: "Letra: **Online + sincronizada
  pela voz** (padrão)" + link-botão **Alterar** que abre o mesmo conjunto de 3 opções como `<select>` inline. Assim quem chegou ao
  modal pelo caminho rápido ainda pode mudar sem voltar.
- Mobile (< 720 px): o split vira dois botões encostados com o mesmo comportamento; o menu abre como folha inferior (bottom
  sheet) com alvos de 48 px.

### 11.4 Chips de origem (AC-08.4)
| Origem (API) | Rótulo longo (modal, editor, title) | Rótulo curto (biblioteca < 1280 px) | Variante | Ícone |
|---|---|---|---|---|
| `aligned` | Online · sincronizada pela voz | Online + voz | `chip-success` | `i-wave` (forma de onda) |
| `whisper` / transcrição | Extraída da voz | Da voz | `chip-info` | `i-mic` |
| `lrclib` (sem alinhamento) | Online | Online | `chip-neutral` | `i-globe` |
| `manual` | Manual | Manual | `chip-accent` | `i-edit` (lápis) |
| `null` | Sem letra | Sem letra | `chip-neutral` tracejado | `i-lyrics` |

- Verde = o melhor estado (texto online **e** tempo pela voz); "Online" puro deixa de ser verde porque é justamente o caso que
  pode estar fora do tempo. Todo chip tem `title` com a explicação completa (ex.: "Letra do LRCLIB com os tempos acertados pela
  voz da música"). A cor nunca é a única pista: sempre há ícone + texto.
- Linhas **interpoladas** (o alinhamento não achou a palavra) aparecem no editor com tempo em itálico e prefixo `≈`
  (`title="Tempo estimado entre as linhas vizinhas"`), sem chip extra na biblioteca.
- Contraste: herdado dos chips existentes (§7) — success 7,84/4,80, info 8,42/4,63, accent 4,89/4,98, neutral (muted sobre
  surface-2) 7,53/6,08 (escuro/claro).

### 11.5 Ações de letra na música (AC-08.5)
**Biblioteca:** o ícone **Letra** vira botão de menu (`aria-haspopup="menu"`), mesmo lugar e tamanho:
```
[+ Fila] [≡▾] [🗑]
          └─ Editar sincronia…            E     ← abre o editor (§11.6)
             Sincronizar pela voz               ← desabilitado sem letra online/colada; title explica
             Extrair da voz
             ─────────────
             Candidatas e colar LRC…            ← modal "Letra" atual
```
**Tocando agora (mini-player):** ao lado do offset rápido, novo botão **Editar sincronia** (ícone `i-edit-time`, `btn-ghost`,
40 px, `title="Editar sincronia (E)"`). Atalho `E` no Controle (fora de campos de texto) abre o editor da música tocando.
O offset rápido `− +120 ms +` continua no mini-player e `[`/`]` no Palco (ajuste de emergência no meio da festa).

**Modal "Letra"** (US-03) muda assim: abas **Candidatas · Colar LRC · Da voz** (a aba "Gerar com Whisper" vira "Da voz" com dois
cartões: **Sincronizar pela voz** — "mantém o texto online, acerta os tempos" — e **Extrair da voz** — "transcreve do zero"). O
bloco "Ajuste de sincronia ±50 ms" sai do modal e no lugar entra a faixa: "Letra fora do tempo? **[Editar sincronia]**
· ou ajuste rápido − +120 ms +". Candidatas mostram o chip de origem da §11.4.

**Estados de job** (as duas ações rodam como job no painel Processamento, com 1 segmento):
| Estado | Biblioteca (linha da música) | Painel Processamento | Ao fim |
|---|---|---|---|
| Na fila | chip info `⏱ Na fila` no lugar do chip de origem | item "aguardando" | — |
| Rodando | chip info `⏱ Sincronizando pela voz 40%` / `Extraindo da voz 40%`; ações do menu Letra desabilitadas com title "Já em andamento" | barra 1 segmento + etapa + % | — |
| Sucesso | chip de origem novo (fade 220 ms) | item some | toast "Letra de 'X' sincronizada pela voz" + **Desfazer** (volta à candidata anterior, `PUT …/selected`) + **Conferir** (abre o editor) |
| Erro | chip origem anterior + ícone ⚠ danger | item vermelho + motivo + Tentar de novo / Descartar | toast erro com motivo e o que fazer ("A voz está muito baixa para alinhar — tente Extrair da voz ou Editar sincronia") |
Se a música estiver tocando, o Palco recarrega a letra no evento `lyrics.updated` (sem reiniciar o áudio).

### 11.6 Editor de sincronia (US-09)
Modal **em tela cheia** do Controle (`role="dialog"`, `aria-modal`, largura `min(var(--editor-max), 100vw − 32px)`,
altura `100vh − 32px`, raio `--radius-xl`). Ao abrir a partir da música que está tocando, o Palco é **pausado**
(toast "Palco pausado enquanto você edita") — o editor toca o áudio no PC.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ✕  Editar sincronia                                  ● 3 alterações  [↶][↷]  [Descartar] [Salvar] │ head
│    Canção da Estrada Vazia · Os Faróis · [Online]                                                 │
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [ Ajustar | Tocar e marcar ]     Velocidade [0,5× | 0,75× | 1×]   [◉ Voz]   [⇣ Mover as seguintes] │ toolbar
├─────────────────────────────────────────────────────────────────────────────────────────────────┤
│ ▁▂▅▃▂▁▁▂▅▇▅▃▂▁▁▂▃▅▃▂▁▁▁▁▁▁▁▁▂▅▇▅▃[▓▓▓▓]▃▂▁▂▅▃▂▁        visão geral (música inteira, 28 px)        │
│      │4              │5                ┃          │6                    │7                       │ janela de 10 s
│ ▂▃▅▇▆▅▃▂▁   ▂▃▅▆▇▆▅▃▂▁▁   ▂▃▅▇▇▆▅▄▃▂▁   ┃  ▁▂▃▅▆▇▆▅▃▂▁▁   ▂▃▅▇▆▅▃▂   (voz separada, 112 px)    │ ┃ = agora
│ (⏮ linha)  ( ▶ )  (⏭ linha)    0:16.80 / 3:31      [−5 s][+5 s]                                  │ transporte
├───────────────────────────────────────────────────────┬─────────────────────────────────────────┤
│ LINHAS · 24                                            │ PRÉVIA                                  │
│  3   0:13.20   O rádio toca aquela canção              │   Que a gente cantava sem ter fim       │ (estilo Palco,
│▌4 ♪ 0:16.80 • Que a gente cantava sem ter fim  ← toca  │   E quando a noite acender o céu        │  24/16 px)
│  5   0:27.50   E quando a noite acender o céu  ← sel.  │─────────────────────────────────────────│
│      [−100][−10]  0:27.50  [+10][+100]  [▶ Ouvir]      │ AJUSTE GERAL (offset)                   │
│  6   0:31.00   Eu vou cantar até o sol nascer          │ [−] ━━━━━●━━━━ [+]   −600 ms   [Zerar]   │
│  7  ≈0:34.60   Luzes da cidade, luzes do farol         │ [✦ Detectar automaticamente]             │
│  …                                                     │ Sugestão: −600 ms · confiança alta       │
│                                                        │ [Aplicar] [Ignorar]                      │
├───────────────────────────────────────────────────────┴─────────────────────────────────────────┤
│ Espaço tocar/pausar · ↑↓ linha · ←→ ±10 ms · Shift+←→ ±100 ms · Enter ouvir · M marcar · Ctrl+Z  ? │ legenda
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```
**Modo "Tocar e marcar"** troca a coluna direita (a prévia continua no topo dela):
```
│ PRÉVIA (idem)                           │
│─────────────────────────────────────────│
│ ┌─────────────────────────────────────┐ │
│ │          MARCAR  [Espaço]           │ │  botão 136 px, magenta, largura total
│ │  “E quando a noite acender o céu”   │ │  = próxima linha a marcar (cursor)
│ └─────────────────────────────────────┘ │
│  Linha 5 de 24      [↑ Voltar uma linha] │
│  Compensar reação: 100 ms  (alterar)     │
│  Esc sai do modo                         │
```

#### Regiões e regras
- **Head:** título + música + chip de origem da candidata em edição. Indicador `● N alterações` (ponto magenta + texto,
  `aria-live="polite"`) só quando houver mudança. **Salvar** = primária única da tela (desabilitada sem alterações);
  **Descartar** = ghost (pede confirmação se houver alterações); ↶/↷ ícones com `aria-label` "Desfazer (Ctrl+Z)"/"Refazer (Ctrl+Shift+Z)".
- **Toolbar:** controle segmentado **Ajustar | Tocar e marcar** (`role="radiogroup"`, atalho `M` alterna); velocidade
  **0,5× · 0,75× · 1×** (`playbackRate` com `preservesPitch=true`; ao entrar no modo marcar sugere 0,75×, lembrado em
  `localStorage`); **Voz** = toggle da voz guia **no editor** (padrão ligado a 100 %: para marcar é preciso ouvir o cantor;
  não altera o valor do Palco); **Mover as seguintes** = toggle (`aria-pressed`, padrão **ligado**, atalho `R`): quando ligado,
  todo deslocamento aplicado a uma linha (ajuste fino ou marca) é aplicado também às linhas **seguintes ainda não marcadas
  nesta sessão**. Motivo: o erro típico do LRCLIB é deslocamento a partir de um ponto (intro/ponte de outra versão), então
  consertar uma linha conserta o resto. Desligue para retocar uma linha isolada.
- **Forma de onda:** da **voz separada** (`vocals.opus`), calculada no navegador (`decodeAudioData` → ~50 picos/s, cache em
  memória). Janela de **10 s** centrada no cursor de reprodução que rola com o áudio; marcadores verticais de início de
  linha (2 px, número da linha em 12 px acima; marcador da linha selecionada/cursor em `--color-accent`, demais em
  `--color-text-subtle`); parte já tocada em `--color-info`, por tocar em `--color-text-subtle`; cursor "agora" 2 px
  `--color-text`. Faixa de **visão geral** com a música inteira e retângulo da janela. **Clique** na onda/visão geral = seek.
  *Should:* arrastar um marcador com o mouse move a linha (alternativa de teclado: ajuste fino). Decodificação falhou ou
  demorou > 4 s → cai para **barra de tempo** simples com os mesmos marcadores (sem bloquear o editor).
- **Transporte:** Play redondo 48 px (`btn-play`), ⏮/⏭ = ir para início da linha anterior/seguinte (1 s antes), −5 s/+5 s,
  tempo `m:ss.cc` tabular.
- **Lista de linhas** (sobre `--color-surface`; linha `--sync-row-h` 48 px; `role="listbox"` com `aria-activedescendant`,
  cada linha `role="option"`): número · tempo `m:ss.cc` (mono tabular) · texto. Estados da linha:
  | Estado | Visual |
  |---|---|
  | Tocando agora (ao vivo) | barra esquerda 3 px `--color-info` + fundo `--color-info-soft` + ícone ♪; texto em `--color-text` |
  | Selecionada (foco/cursor) | fundo `--color-accent-soft` + anel magenta 1 px; abre a sub-linha de **ajuste fino** `[−100] [−10] tempo [+10] [+100] [▶ Ouvir]` |
  | Alterada nesta sessão | ponto magenta 6 px antes do tempo (`title="Alterada — antes 0:28.10"`) |
  | Recém-marcada | flash `--color-info-soft` → transparente em 400 ms (sem flash com reduced-motion; o ponto basta) |
  | Interpolada | tempo em itálico `--color-text-muted` com `≈` |
  | Sem tempo | `—:—.—` em `--color-text-subtle` (letra colada sem tempos) |
  | Fora de ordem | tempo em `--color-warning` + ícone ⚠ + title "Começa antes da linha anterior"; Salvar não grava: mostra o aviso "2 linhas fora de ordem" e seleciona a 1ª delas |
  A lista rola sozinha para manter a linha tocando visível **enquanto o usuário não estiver rolando** (pausa a auto-rolagem
  por 4 s após wheel/scroll manual). Clicar numa linha = selecionar **e** seek para 1 s antes + tocar (AC-09.3).
- **Prévia:** 2 linhas no estilo do Palco (atual 24 px peso 800 com preenchimento ciano por palavra; próxima 16 px muted),
  fundo `--stage-bg` com raio `--radius-lg` — mostra exatamente como vai sair na TV (mesmo algoritmo da §6, com o offset).
- **Ajuste geral (offset):** slider −5000…+5000 ms passo 10 (trilha ciano), botões −/+ (±50 ms), valor `+120 ms`, **Zerar**.
  Prévia ao vivo (lista, marcadores e prévia andam juntos). Convenção igual à §6: positivo = letra mais tarde.
  **Detectar automaticamente** (`POST …/lyrics/detect-offset`): botão secundário com ícone `i-wand`; enquanto calcula, ícone
  gira e o texto vira "Ouvindo a voz…"; resultado vira caixa `callout info` "Sugestão: **−600 ms** · confiança alta"
  + **Aplicar** (primária pequena) / **Ignorar**. Confiança baixa → callout warning "Sugestão incerta: −600 ms. Ouça antes de
  salvar." Falha → "Não deu para detectar: a voz está baixa ou a letra é de outra versão. Tente **Sincronizar pela voz** ou
  marque as linhas." Aplicar é uma ação desfazível.
- **Legenda de atalhos** fixa no rodapé (sempre visível, `kbd`), muda conforme o modo; `?` abre a lista completa (popover).

#### Tocar e marcar (AC-09.2)
1. Entrar no modo (`M` ou segmento): cursor = linha selecionada (ou a 1ª sem tempo / a 1ª). Toca a partir de **3 s antes**
   do tempo atual do cursor (ou do início) com contagem "3·2·1" no botão.
2. **Espaço / Enter / clique no botão MARCAR** grava `tempo = agora − compensação de reação` (padrão **100 ms**, ajustável
   0–300 ms no link "alterar", lembrado em `localStorage`) na linha do cursor, aplica "Mover as seguintes" se ligado,
   avança o cursor e dá feedback: botão afunda (80 ms), linha pisca (400 ms), texto do botão troca para a próxima linha.
   Leitores de tela: `aria-live` "Linha 5 marcada em 0:27,48".
3. **↑ / Backspace / "Voltar uma linha"**: cursor volta 1 linha e o áudio volta para 2 s antes do tempo dessa linha — é só
   apertar de novo para remarcar. Marcar a mesma linha outra vez substitui (e é desfazível).
4. Após a última linha o modo sai sozinho: toast "Todas as linhas marcadas — ouça para conferir" e a reprodução segue no
   modo Ajustar. `Esc` sai do modo a qualquer momento (pausa). `K` pausa/continua dentro do modo (Espaço está reservado para marcar).
5. Mobile: o botão MARCAR fica **fixo no rodapé** (largura total, 96 px) com polegar.

#### Desfazer, salvar, sair (AC-09.5)
- Pilha de desfazer por sessão: cada marca, ajuste, offset, "Aplicar sugestão" é um passo; toques seguidos de ±10/±100 na
  mesma linha em < 600 ms são agrupados num só passo. `Ctrl+Z` / `Ctrl+Shift+Z` (e `Ctrl+Y`).
- **Salvar** (`Ctrl+S`):
  - mudou algum tempo de linha → `PUT /api/songs/{id}/lyrics/manual-timing` com as linhas (palavras deslocadas junto com a
    linha; se a origem não tem palavras, o servidor redistribui) → cria/atualiza a candidata **Manual** (`derived_from` = a
    original) e a seleciona. Chip passa a "Manual". Toast "Sincronia salva — a original continua em Letra › Candidatas".
  - mudou só o offset → `PATCH /api/songs/{id} {offset_ms}`; não cria candidata nova.
  - Durante: botão com spinner "Salvando…", editor continua visível mas não editável (`aria-busy`). Erro: banner danger no
    topo do editor "Não deu para salvar: <motivo>. Suas alterações continuam aqui." + **Tentar de novo**; nada é perdido.
- **Sair com alterações** (✕, Esc fora de modo, Descartar, clique fora **não fecha** este modal): diálogo interno (não `confirm()`):
  "Sair sem salvar? Você tem 3 alterações nesta música." **[Continuar editando]** (foco inicial) · **[Descartar]** (danger-ghost)
  · **[Salvar e sair]** (primária). `beforeunload` também avisa.

#### Estados do editor
| Estado | Conteúdo |
|---|---|
| Carregando | head normal; skeleton da onda (shimmer) + 8 linhas skeleton; legenda "Preparando a forma de onda da voz…"; play desabilitado até o áudio ter `canplay` (lista aparece assim que a letra chega, a onda pode vir depois) |
| Sem letra | estado vazio no lugar da lista: ícone letra, "Esta música ainda não tem letra", 3 caminhos: **Extrair da voz** (primária) · **Colar a letra** (abre textarea **sem tempos**: uma linha por verso → vira lista com `—:—` pronta para Tocar e marcar) · "Buscar no LRCLIB" (abre Candidatas) |
| Pronto / sem alterações | Salvar desabilitado; legenda de atalhos |
| Alterações não salvas | `● N alterações`, Salvar habilitado, `beforeunload` ativo |
| Salvando | Salvar com spinner, `aria-busy` |
| Erro ao salvar | banner danger + Tentar de novo |
| Áudio falhou | callout danger no lugar do transporte: "Não deu para tocar o áudio desta música. Reprocesse em Biblioteca › Letra." (a lista continua editável à mão) |
| Job de letra rodando para esta música | banner info "Sincronizando pela voz… o editor recarrega quando terminar" e edição bloqueada |

#### Atalhos do editor
| Tecla | Ajustar | Tocar e marcar |
|---|---|---|
| `Espaço` | tocar/pausar | **marcar** linha do cursor |
| `Enter` | ouvir a linha selecionada (seek 1 s antes) | marcar |
| `K` | tocar/pausar | tocar/pausar |
| `↑` / `↓` | linha anterior/seguinte | `↑` volta uma linha (remarcar) |
| `Backspace` | — | volta uma linha |
| `←` / `→` | linha selecionada −10/+10 ms | — |
| `Shift+←` / `Shift+→` | −100/+100 ms | — |
| `Alt+←` / `Alt+→` | voltar/avançar 5 s | voltar/avançar 5 s |
| `[` / `]` | offset geral −50/+50 ms | idem |
| `M` | alternar modo | alternar modo |
| `R` | Mover as seguintes liga/desliga | idem |
| `1` `2` `3` | velocidade 0,5× / 0,75× / 1× | idem |
| `Ctrl+Z` / `Ctrl+Shift+Z` | desfazer / refazer | idem |
| `Ctrl+S` | salvar | salvar |
| `Esc` | fechar (confirma se houver alterações) | sair do modo |
| `?` | lista de atalhos | idem |
Ordem de tabulação: head (fechar → desfazer/refazer → Descartar → Salvar) → toolbar → onda (foco = `←`/`→` move 1 s) →
transporte → lista (um único tab stop; setas dentro) → coluna direita → legenda. Os atalhos de letra única só valem com o
foco fora de campos de texto/slider.

### 11.7 Fluxo: "letra online fora do tempo → corrigida em < 2 min"
Cenário: "Canção da Estrada Vazia" tocando, a letra chega ~0,6 s atrasada e, depois da ponte, mais 1,5 s.
1. **(0:00)** No mini-player, clica **Editar sincronia** (ou `E`). Palco pausa; editor abre já na posição atual com a linha
   tocando destacada. Chip "Online".
2. **(0:05)** Callout no topo da coluna direita (só para origem "Online"): "Esta letra ainda não foi acertada pela voz.
   **[Sincronizar pela voz]** resolve sozinho em ~20 s." → 1º caminho. Se clicar: job roda dentro do editor (banner),
   recarrega com chip "Online · sincronizada pela voz"; ouvir 2–3 linhas clicando nelas; **Salvar não é necessário** (já
   virou candidata). Fim em ~40 s.
3. **(alternativa manual, 0:10)** **Detectar automaticamente** → "Sugestão: −600 ms" → **Aplicar**. Clica na linha 2 → ouve
   (seek 1 s antes) → bate. (~30 s)
4. **(0:45)** Clica na linha 7 (depois da ponte) → ouve → ainda atrasada. Aperta `M` (Tocar e marcar, cursor na linha 7, 0,75×,
   "Mover as seguintes" ligado) → toca 3 s antes → **Espaço** quando a voz entra. Linhas 7…fim se deslocam juntas. `Esc`.
5. **(1:15)** Clica na linha 10 e na última para conferir. Uma linha isolada adiantada? Seleciona e `Shift+→` duas vezes
   (+200 ms) com "Mover as seguintes" desligado (`R`).
6. **(1:40)** **Salvar** (`Ctrl+S`) → candidata "Manual" selecionada, toast com caminho de volta. Fecha; o Palco recarrega a
   letra e o usuário retoma com Espaço na TV.
Marcar a música inteira linha a linha (dura ≈ duração ÷ velocidade) fica reservado ao caso "letra colada sem tempos".

### 11.8 Componentes novos
| Componente | Variações | Estados | Notas |
|---|---|---|---|
| Split button | primária + ▾ | hover/focus/active por segmento, expanded | ▾ 40 px; separador `--color-on-accent` 25 % |
| Menu (dropdown) | ações, radio (`menuitemradio`) | item hover/focus (`surface-3`), checked (✓ accent), disabled (45 % + title) | `--shadow-3`, raio `--radius-md`, 8 px padding, itens 40 px; seção com cabeçalho caps |
| Radio card | padrão, recomendado (chip success) | hover (`surface-2`), checked (borda accent + `accent-soft`), focus (anel no card) | label inteiro clicável |
| Chip de override | `Letra: <modo> ✕` | ✕ hover/focus | neutro; ✕ 24 px com `aria-label="Usar o padrão"` |
| Segmented control | modo do editor, velocidade | hover, checked (`surface-3` + texto `--color-text` + sublinhado magenta 2 px), focus | `role="radiogroup"`, setas trocam |
| Toggle botão | Voz, Mover as seguintes | `aria-pressed` (= `.btn.toggle` existente) | |
| Waveform | onda / barra simples (fallback) | carregando (shimmer), foco (anel), hover (cursor fantasma 1 px + tempo) | canvas; cores lidas dos tokens via `getComputedStyle` e redesenhadas no `settings.updated`/troca de tema |
| Sync row | ver tabela da §11.6 | | mono tabular para tempos |
| Fine stepper | −100 −10 [tempo] +10 +100 + Ouvir | hover, focus, active | botões 40 px; `aria-label` "Linha 5 dez milissegundos mais cedo" |
| Mark pad | normal, contagem, pressionado, concluído | active (scale .98, 80 ms), focus | `btn-primary` 136 px (`--mark-pad-h`), texto 18 px + linha alvo 16 px; `aria-keyshortcuts="Space Enter"` |
| Stage preview | 2 linhas | — | fundo `--stage-bg`, texto `--stage-text`/`--stage-text-sung` (contraste do Palco §7) |
| Unsaved indicator | — | aparece/some 220 ms | ponto accent + texto `--color-text-muted` |

**Ícones novos** (mesmo set, traço 2 px, grade 24 — copiar do protótipo para `static/icons.svg`): `i-gear`, `i-wave`,
`i-globe`, `i-edit`, `i-edit-time`, `i-undo`, `i-redo`, `i-wand`, `i-chev` (▾), `i-ripple` (seguintes).

**Tokens novos** (`design/tokens.css`, só tamanhos — nenhuma cor nova): `--editor-max` 1280 px, `--waveform-h` 112 px,
`--overview-h` 28 px, `--mark-pad-h` 136 px, `--sync-row-h` 48 px.

### 11.9 Contraste dos pares novos (calculado)
| Par | Escuro | Claro | Uso |
|---|---|---|---|
| onda por tocar `--color-text-subtle` / surface-2 | 4,88 | 4,41 | componente (≥ 3) |
| onda tocada `--color-info` / surface-2 | 10,23 | 4,54 | componente |
| marcador `--color-accent` / surface-2 | 5,26 | 5,01 | componente |
| texto / linha selecionada (`accent-soft` sobre surface) | 12,27 | 12,80 | texto |
| muted / linha selecionada | 6,28 | 5,19 | número/tempo |
| texto / linha tocando (`info-soft` sobre surface) | 11,34 | 13,14 | texto |
| aviso `--color-warning` / surface-2 (tempo fora de ordem) | 10,19 | 4,89 | texto 14 px |
⚠ `--color-accent` **como texto** sobre `accent-soft` dá 4,38/4,27 (< 4,5): não usar texto magenta pequeno dentro da linha
selecionada — o realce é o fundo + anel; texto continua `--color-text`. Idem `--color-info` como texto sobre `info-soft` no
tema claro em surface-2 (3,98): a lista fica sobre `--color-surface`, onde o par vale 4,63.

### 11.10 Responsivo do editor
- **≥ 1100 px:** layout acima (lista `minmax(0,1fr)` + coluna direita 360 px).
- **720–1100 px:** coluna direita desce para baixo da onda como faixa horizontal (prévia à esquerda, offset/marcar à direita);
  lista ocupa o resto.
- **360–720 px:** modal = tela inteira sem raio; toolbar rola horizontalmente **dentro** de si (sem scroll da página);
  onda 80 px; prévia escondida (a linha tocando já se destaca na lista); ajuste fino em linha própria; MARCAR fixo no rodapé.

### 11.11 Notas para frontend/backend
- `lyrics_mode` em Settings e no `POST /api/jobs`; origem `aligned` nas candidatas; rotas `…/lyrics/align`, `…/transcribe`,
  `…/manual-timing`, `…/detect-offset` (nomes conforme T-001 — se o contrato final mudar, os rótulos da UI não mudam).
- Para o ponto "alterada" e o ≈ de interpolada, a UI precisa do tempo original da linha (vem da candidata carregada) e de uma
  marca por linha `interpolated: true` no documento Lyrics da origem `aligned` (pedido ao backend/architect, ver HANDOFF).
- O editor usa **seu próprio** `<audio>`/`AudioContext` (instrumental + voz) no Controle; não usa o relógio do Palco.
  Relógio = `audio.currentTime` (com `playbackRate`); destacar linha em `requestAnimationFrame`.
- Produção sem handlers inline (CSP), como no resto do app; a onda em `<canvas>` com cores lidas dos tokens.
