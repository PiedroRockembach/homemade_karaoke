# ADR 0004 — Mecânica de Pontuação, Guia de Notas e QR Code Contínuo

- **Status**: Proposto (Ciclo `20261017-1115-karaoke`)
- **Data**: 2026-10-17
- **Autores**: architect, orchestrator

---

## 1. Contexto

No ciclo 3 (v0.3.0) entregamos o modo convidado com QR Code no Palco, porém o QR code era ocultado durante a
execução da música para manter a tela limpa. Em festas reais, novos convidados chegam ou decidem pedir músicas
enquanto alguém está cantando, gerando atrito por precisarem esperar a música terminar.

Além disso, adicionamos a mecânica de pontuação interativa (gamificação), onde as notas da melodia vocal são
representadas em barras (piano roll) e a voz do cantor é capturada via microfone para pontuar afinação em
tempo real, com opção de ligar/desligar.

---

## 2. Decisões Arquiteturais

### 2.1. Extração de Pitch das Músicas (Melodia de Referência)

- **Origem do áudio**: Cada música preparada já possui `vocals.opus` isolado com alta fidelidade pelo Demucs.
- **Algoritmo**: Algoritmo YIN (de-facto standard para monofonia de voz) implementado em Python com
  NumPy/PyTorch.
  - Divide a faixa vocal em janelas de 30 ms com salto (hop) de 10-20 ms.
  - Converte frequências fundamentais em notas MIDI quantizadas (36 a 84, C2 a C6) e agrupa notas consecutivas
    estáveis em blocos `[start, end, midi, note]`.
  - Quando houver alinhamento de palavras (`lyrics.json` com `timing == "word"` ou `transcript.json`), associa
    o texto de cada sílaba/palavra à sua respectiva nota.
- **Armazenamento e Cache**:
  - Salvo em `data/songs/<id>/pitch.json`.
  - Endpoint: `GET /api/songs/{id}/pitch` retorna o JSON de notas.
  - Cálculo sob demanda para músicas já existentes, em cache perpétuo no disco.

### 2.2. Detecção de Voz do Cantor em Tempo Real no Frontend

> **Substituído pela emenda de 2026-10-21** (ver §4.1).

- **Captura**: Web Audio API no Palco (`navigator.mediaDevices.getUserMedia({ audio: true })`).
- **Processamento de Áudio**:
  - `AudioContext` com `AnalyserNode` obtendo amostras de áudio (2048 amostras a 44.1/48 kHz).
  - Algoritmo de autocorrelação com interpolação parabólica (ou YIN leve) rodando a 60 FPS dentro de
    `requestAnimationFrame` no JavaScript do Palco.
  - Rejeição de ruído: threshold de RMS / volume para ignorar silêncio ou eco da sala.
  - **Privacidade & Latência**: 100% no cliente. Nenhum byte do microfone trafega pela rede ou chega ao
    servidor.

### 2.3. Tolerância Musical e Sistema de Pontuação

> **Substituído pela emenda de 2026-10-21** (ver §4.2). O teto de 10.000 pontos continua valendo.

- Tolerância de afinação: ±1 semitom ou 75 cents.
- Tolerância de oitava: Se o cantor canta uma oitava abaixo ou acima (ex.: mulher cantando canção de tenor ou
  homem cantando canção de soprano), o desvio é verificado módulo 12:
  $$\Delta = |(\text{midi}_{\text{cantado}} - \text{midi}_{\text{alvo}}) \pmod{12}|$$
  Se $\Delta \le 1$ ou $\Delta \ge 11$, a nota é considerada afinada! Isso torna a brincadeira acessível para
  qualquer extensão vocal.
- Pontuação total máxima: 10.000 pontos normalizada pelo tempo cantado.

### 2.4. QR Code Contínuo no Palco

- Modificação na classe `GuestQr` (`js/stage/guest-qr.js`):
  - Em vez de restringir a `BETWEEN_SONGS`, o QR permanece ativo sempre que `guestState.enabled == true`.
  - Cena `play`: tamanho `mini` (`qr-mini`): dimensão de 130×130px posicionada no canto inferior direito, com
    moldura translúcida escura e texto "Peça pelo celular" e URL.
  - Cenas de intervalo (`idle`, `upnext`, `start`, `prep`): mantém o layout grande / padrão.
  - Desligar modo convidado: transição imediata para invisível via SSE.

### 2.5. Configurações e Persistência

- Adição da coluna/chave `scoring_enabled` (booleano) em `Settings`.
- Sincronização via `PATCH /api/settings` e evento SSE `settings.updated`.
- Atalho `S` no Palco para toggle rápido da pontuação.

---

## 3. Consequências

- **Positivas**:
  - Convidados podem se conectar ao Karaokê a qualquer momento da festa.
  - Experiência visual rica e competitiva para quem gosta de pontuação, sem prejudicar quem prefere o karaokê
    tradicional (desativável com um clique ou tecla `S`).
  - Sem latência de rede na detecção do microfone e zero uso de CPU no backend durante a festa.
- **Neutras**:
  - Requer autorização de microfone no navegador do Palco quando a pontuação for ativada pela primeira vez.

---

## 4. Emenda 2026-10-21 (ciclo 20261021-0030-karaoke)

- **Status**: Aceito.
- **Fontes**: `.agents/runtime/20261021-0030-karaoke/BRIEF.md`, `SPEC.md` (US-25, US-26, US-27) e
  `messages/0002-frontend-engineer-orchestrator-HANDOFF.md`.
- **Motivo**: o indicador tremia e saltava de oitava (`foldOctave`), a pontuação aceitava qualquer oitava com
  até ~±1,5 semitom, e a captação com supressão de ruído e ganho automático distorcia a voz.

### 4.1. Detecção no navegador (substitui §2.2)

- **Captação** (`js/stage/mic-pitch.js`, `MIC_CONSTRAINTS`): `echoCancellation: true` (reduz a música tocada
  pela própria página), `noiseSuppression: false` e `autoGainControl: false` (não distorcem a voz nem fazem o
  nível oscilar).
- **Detector**: YIN executado a cada quadro de `requestAnimationFrame`, com confiança = 1 − CMND e conversão
  frequência → MIDI contínua (sem arredondar).
- **Suavização** (`js/stage/pitch-smooth.js`, `PitchSmoother`, parâmetros como constantes nomeadas):
  - histerese sonoro/silêncio por nível e confiança: `RMS_ON` 0,012 / `RMS_OFF` 0,007, confiança 0,8 / 0,6;
  - rejeição de salto: desvios acima de 500 cents que não se repetem por 3 leituras são descartados;
  - mediana de 3 leituras;
  - EMA em cents, ponderada pelo tempo: lenta (τ 70 ms) perto do valor e rápida (τ 15 ms) em transições, com
    histerese entre os dois modos;
  - ao calar, o ponto segura 120 ms (hold) e some em 150 ms; durante o hold `voiced` já é falso e não pontua.
- **Privacidade**: inalterada, 100% no cliente.

### 4.2. Tolerância e oitava (substitui §2.3)

- **Acerto**: |cantado − alvo| ≤ 50 cents, em cents contínuos, **sem** equivalência de oitava. Só a voz
  filtrada e `voiced` pontua. Teto de 10.000 pontos mantido.
- **Oitava**: o indicador mostra sempre a altura real. A tecla **O** desloca a régua 0 → −1 → +1 → 0 oitava;
  barras e pontuação usam `midi + 12·shift`. O HUD avisa e a régua mostra um rótulo quando o deslocamento é
  diferente de 0. O valor fica salvo em `localStorage["karaoke.stage.octaveShift"]` (com try/catch). Decisão
  do usuário.
- **Alternativas rejeitadas**:
  - dobra automática nota a nota (`foldOctave`): o ponto saltava 12 semitons quando o alvo mudava ou a voz
    cruzava meia oitava;
  - detecção automática de oitava: imprevisível e fora de escopo (SPEC).

### 4.3. Consequências

- Indicador estável e contínuo; pontuação mais exigente e previsível.
- Quem canta em outra oitava precisa apertar O uma vez.
- Os limiares RMS não foram medidos com microfone real; ajustar `RMS_ON`/`RMS_OFF` se o indicador não ligar.
- O YIN custa ~42 M operações/s a 60 FPS; se pesar, analisar a cada 2 quadros.
- A voz-guia que sai da caixa ainda pode pontuar (fora de escopo).
