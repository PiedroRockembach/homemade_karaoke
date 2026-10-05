# Contrato — formato da letra sincronizada (v1)

> Dono: architect · Ciclo `20261003-2339-karaoke`. Consumido pelo Palco via `GET /api/songs/{id}/lyrics`
> ([api.md §7](api.md#7-letras)). Independente da origem (LRCLIB, Whisper ou LRC colado).
> **v0.2** (ciclo `20261004-1742-karaoke`, T-001): origem `aligned`, campo opcional `interpolated` por linha (§3.1),
> candidatas `manual` do editor de sincronia (§3.2). O formato continua `version: 1` (só acréscimos compatíveis).
> **CR-001** (T-006): candidatas só texto (`online_plain`, colada sem tempos) — §3.3.

## 1. Documento

```jsonc
{
  "version": 1,
  "song_id": "fJ9rUzIMcZQ",
  "candidate_id": "lrclib:28374",
  "source": "lrclib",              // "lrclib" | "whisper" | "manual" | "aligned" (v0.2) | null (sem letra)
                                   // ("online_plain" existe só como candidata não selecionável — §3.3)
  "timing": "line",                // "word" = todas as linhas têm words; "line" = só tempos de linha; "none" = sem letra
  "language": "en",                // ISO 639-1 ou null
  "duration_ms": 359000,           // duração do áudio da música (não da letra)
  "offset_ms": 0,                  // offset salvo da música (ver §4) — conveniência; a fonte é Song.offset_ms / PlayerState.offset_ms
  "lines": [
    {
      "i": 0,
      "start_ms": 12340,
      "end_ms": 15200,
      "text": "Is this the real life?",
      "words": null                // ou lista (ver abaixo) quando timing = "word" ou linha veio de Enhanced LRC
      // "interpolated": true        // v0.2, opcional: só presente (true) quando o tempo da linha foi ESTIMADO (§3.1)
    },
    {
      "i": 1,
      "start_ms": 15800,
      "end_ms": 18900,
      "text": "Is this just fantasy?",
      "words": [
        { "text": "Is",        "start_ms": 15800, "end_ms": 16050 },
        { "text": "this",      "start_ms": 16050, "end_ms": 16400 },
        { "text": "just",      "start_ms": 16400, "end_ms": 16900 },
        { "text": "fantasy?",  "start_ms": 16900, "end_ms": 18900 }
      ]
    }
  ],
  "meta": {
    "track_name": "Bohemian Rhapsody",
    "artist_name": "Queen",
    "album_name": "A Night at the Opera",
    "provider_id": 28374,          // id no LRCLIB (null para whisper/manual)
    "model": null,                 // ex. "large-v3-turbo" quando source = whisper
    "created_at": "2026-10-03T23:51:20Z"
  }
}
```

## 2. Invariantes (garantidos pelo backend)

1. `lines` ordenadas por `start_ms`, `i` = índice 0..n-1.
2. `0 ≤ start_ms < end_ms`; `end_ms ≤ lines[i+1].start_ms` (linhas **não se sobrepõem**).
3. `text` não vazio, sem espaços nas pontas. Linhas vazias do LRC (marcadores de instrumental) **não** viram linhas —
   viram intervalos (§5).
4. Quando `words` existe: não vazia; ordenada; `words[0].start_ms == start_ms`; `words[-1].end_ms == end_ms`;
   palavras contíguas ou com lacunas, nunca sobrepostas; `text` da linha == palavras unidas por um espaço
   (`words.map(w => w.text).join(" ")`). Pontuação fica grudada na palavra.
5. `end_ms` de linha sem palavras (LRC comum) é estimado: `end_ms = min(próximo timestamp do LRC (inclusive marcadores vazios), start_ms + clamp(palavras × 450 + 600, 1500, 8000))`;
   para a última linha, limitado também por `duration_ms`.
6. Tempos já incluem a tag `[offset:]` do LRC, se houver. **Não** incluem `offset_ms` da música (aplicado no cliente).
7. Whisper: segmentos longos são quebrados em linhas de no máx. ~42 caracteres ou em pausas > 600 ms entre palavras;
   alucinações típicas (texto repetido, `no_speech_prob` alto) são descartadas.

## 3. Origem → documento

| Origem | `timing` | `words` |
|--------|----------|---------|
| LRCLIB `syncedLyrics` (LRC comum) | `line` | `null` |
| LRCLIB / manual em Enhanced LRC (`[00:12.34]<00:12.34>Is <00:12.50>this …`) | `word` se todas as linhas tiverem tags, senão `line` (linhas com tags mantêm `words`) | por linha |
| Whisper (`word_timestamps=True`) | `word` | sempre |
| Alinhada pela voz (`aligned`, v0.2) | `word` | sempre |
| Editor de sincronia (`manual`, v0.2) | `word` | sempre (enviadas, reescaladas da base ou distribuídas) |
| Sem letra | `none` | `lines: []` |

### 3.1 Origem `aligned` (v0.2, AC-08.3)
- **Texto** = o da candidata de origem (`lrclib`/`manual`), linha a linha (pontuação e grafia preservadas); **tempos de
  linha e palavra** = do Whisper sobre o vocal separado, por alinhamento de sequência (Needleman-Wunsch sobre palavras
  normalizadas — minúsculas, sem acento/pontuação — com similaridade difusa e penalidade de tempo que desempata
  refrões repetidos). Inícios de palavra do Whisper que absorveram silêncio/respiração são corrigidos pelo envelope de
  energia do vocal.
- Linha **ancorada**: palavras suficientes casadas (≥ 1 se ≤ 2 palavras; ≥ 2 se ≤ 4; ≥ 40 % acima disso) e monotônica.
  Palavras não casadas dentro dela são interpoladas por caractere entre as vizinhas.
- Linha **não ancorada** → `"interpolated": true`: início interpolado linearmente (no tempo do LRC) entre as âncoras
  vizinhas; nas pontas, tempo do LRC + deslocamento da âncora mais próxima; sem âncora nenhuma, tempo do LRC
  (último recurso). Palavras divididas por caractere na duração original da linha. O editor (DESIGN §11.11) sinaliza
  essas linhas. Ausente = tempo veio da voz (ou da fonte, nas demais origens).
- Linhas que começariam depois de `duration_ms` são descartadas; invariantes do §2 garantidas.
- O offset da música (§4) continua valendo por cima (normalmente ~0 numa `aligned`).

### 3.2 Origem `manual` do editor (v0.2, AC-09.5)
Criada por `PUT /lyrics/manual-timing` a partir de outra candidata (`derived_from` no resumo da candidata — api.md §7);
`interpolated` não é propagado (os tempos agora são do usuário). Não substitui nem apaga a candidata de origem.

### 3.3 Candidatas só texto (CR-001)
LRCLIB com `plainLyrics` e sem `syncedLyrics` (origem `online_plain`) e letra colada sem nenhuma tag de tempo (origem
`manual`) são guardadas com `timing: "none"`, `lines: []` e o texto em `text_lines: ["linha 1", …]` (linhas não vazias,
≤ 500 caracteres). **Não são selecionáveis** (`409 candidate_not_selectable`) e nunca chegam ao Palco: servem de texto
para o alinhamento pela voz, que gera uma `aligned` (`derived_from` = a só texto). No alinhamento sem tempos de
referência, as linhas recebem tempos estimados espalhados por caracteres entre a 1ª e a última palavra cantada (só
para interpolar as que não casarem — essas saem com `interpolated: true`).

## 4. Relógio e offset (regra única para todas as telas)

```
t_letra_ms = posição_áudio_ms − latência_saída_ms − offset_ms
```
- `posição_áudio_ms`: do AudioEngine (`AudioContext`), nunca de `setInterval`/`Date`.
- `latência_saída_ms`: `(ctx.outputLatency || ctx.baseLatency || 0) × 1000`.
- `offset_ms` **positivo = letra atrasada** (aparece mais tarde); negativo = letra adiantada.
  Tecla `]` → `offset_relative +50` ("letra mais tarde"), `[` → `-50` ("letra mais cedo").

## 5. Algoritmo de renderização (referência para o Palco)

```js
// linha atual: última linha com start_ms <= t (busca binária). Pode ser null antes da 1ª linha.
// "ativa" se t < line.end_ms; entre end_ms e a próxima start_ms a linha fica 100% preenchida (ou some, a critério do DESIGN).
function lineProgress(line, t) {
  if (t <= line.start_ms) return 0;
  if (t >= line.end_ms) return 1;
  if (line.words) {
    // por palavra: palavras anteriores 100%, atual parcial, seguintes 0%
    // progresso da linha ponderado por número de caracteres de cada palavra
    let done = 0, total = 0;
    for (const w of line.words) {
      const len = w.text.length + 1; total += len;
      if (t >= w.end_ms) done += len;
      else if (t > w.start_ms) done += len * (t - w.start_ms) / (w.end_ms - w.start_ms);
    }
    return done / total;
  }
  // sem palavras: interpolação linear por caractere ao longo da linha
  return (t - line.start_ms) / (line.end_ms - line.start_ms);
}
```
- Preenchimento visual recomendado: gradiente/`clip` em `%` de largura do texto via CSS custom property
  (`--fill: 0..1`) atualizada em `requestAnimationFrame`.
- **Intervalo instrumental** (AC-06.3): se `próxima.start_ms − t > 5000` (ou antes da 1ª linha com
  `lines[0].start_ms − t > 5000`), mostrar indicador de espera com contagem regressiva até `próxima.start_ms`
  (ex.: pontos/3-2-1 nos últimos 3 s).
- **Próxima linha**: sempre `lines[atual+1]` (ou `lines[0]` antes do início).
- Sem letra (`timing: "none"`): Palco mostra título/artista e o aviso "Letra indisponível".

## 6. Exemplos mínimos

LRC colado:
```
[ti:Bohemian Rhapsody]
[ar:Queen]
[00:12.34]Is this the real life?
[00:15.80]Is this just fantasy?
[00:19.00]
[00:45.10]Mama, just killed a man
```
→ 3 linhas (`timing: "line"`). A linha 1 termina em `min(19000, 15800 + clamp(4×450+600, 1500, 8000)) = 18200`
(regra 5 de §2); de 18,2 s a 45,1 s há intervalo instrumental (> 5 s → indicador de espera).
