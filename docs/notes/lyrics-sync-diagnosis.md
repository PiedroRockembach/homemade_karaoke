# Diagnóstico — sincronia das letras do LRCLIB (US-10 / AC-10.1, AC-08.6)

> Ciclo `20261004-1742-karaoke` · T-001 · backend-engineer · 2026-10-04
> Medido sobre uma **cópia** de `data/` (o banco e os arquivos do usuário não foram alterados).

## Resumo (3 linhas)
1. **Não há bug de parse nem de offset.** `[mm:ss.xx]`/`[mm:ss.xxx]`, `[offset:]` e o fim de linha são interpretados
   como manda o contrato; as 3 músicas usam `[mm:ss.xx]` e nenhuma traz `[offset:]`.
2. **A causa são os próprios tempos do LRCLIB** em relação a estas gravações do YouTube: Ara Ketu tem a letra ~1,2 s
   adiantada (offset constante + deriva leve), Rita Lee tem **deriva** de −1,3 s/min (nenhum offset constante resolve),
   e mesmo na melhor (Perfect) sobra o "tremor" de batida manual (~140 ms de média, picos de 0,5 s).
3. **O modo `aligned` (texto do LRCLIB + tempos da voz) resolve:** desvio médio de início de linha 0 ms vs Whisper
   (por construção) e **22 / 115 / 140 ms** vs ataque real da voz no vocal (meta ≤ 150 ms), em 60–190 ms de CPU.

## Método
- Transcrição faster-whisper `large-v3-turbo` (GPU, `word_timestamps`, VAD) do `vocals.opus` de cada música + envelope
  de energia do vocal (dB a cada 10 ms) — o mesmo que o pipeline grava em `songs/<id>/transcript.json`.
- **Referência por linha (Whisper):** início da 1ª palavra da linha na transcrição, quando ela casa no alinhamento de
  sequência (Needleman-Wunsch, palavras normalizadas) junto com a 2ª palavra ou ≥ 3 palavras da linha. Linhas sem
  casamento confiável ficam fora (por isso `n` < nº de linhas).
- **Referência independente (ataque de voz):** subida de energia ≥ 12 dB no vocal separado mais próxima (±1 s) do
  início medido. Serve para checar o próprio Whisper, já que `aligned` usa os tempos dele.
- Sinal: `d = início da letra − início da voz`; **d < 0 = letra adiantada** (aparece antes de cantar).
- Ferramenta reprodutível: `karaoke/tools/lyrics_sync_report.py` (comandos no fim).

## O que foi verificado no código
| Item | Resultado |
|------|-----------|
| `[mm:ss.xx]` vs `[mm:ss.xxx]` | `_to_ms` completa à direita (`.5`→500, `.34`→340, `.345`→345). Testado (`test_lrc_parser`). OK |
| `[offset:+n]` | Convenção LRC (positivo = letra adiantada → subtrai). Nenhuma das 3 letras tem a tag. OK |
| Fim de linha | `min(próximo timestamp, inclusive marcador vazio; início + clamp(palavras×450+600, 1,5–8 s))` — só afeta o fim do preenchimento, não o início. OK |
| Escolha de candidata | Todas as candidatas de cada música têm praticamente os **mesmos tempos** (Perfect: 8 idênticas; Rita Lee: ±30 ms entre as 8; Ara Ketu: a 2ª candidata, Δduração −5,6 s, também está ~1 s adiantada). Trocar de candidata não resolve. OK |
| Offset salvo pelo usuário | Rita Lee +200 ms, Ara Ketu +700 ms: direção certa (letra mais tarde), mas insuficiente (Ara Ketu precisaria ~+1200) e inútil contra deriva (Rita Lee). |

## Números — LRCLIB (candidata selecionada) × voz
| Música (id) | LRC − voz: médio \|d\| / mediana / máx | Melhor offset constante → médio \|d\| / máx | Deriva | Resíduo após reta (médio / máx) |
|---|---|---|---|---|
| Rita Lee — Minha vida (`ImKODXtNK4g`, `lrclib:1`) | 813 / −570 / 1830 ms (n=21) | +570 ms → 659 / 1940 ms | **−1336 ms/min** | 196 / 772 ms |
| Ed Sheeran — Perfect (`cNGjD0VG4R8`, `lrclib:11`) | 149 / −50 / 510 ms (n=40) | +50 ms → 139 / 560 ms | ~0 | 146 / 520 ms |
| Ara Ketu — Sempre Será (`W8Shyv8OmXw`, `lrclib:19`) | 1266 / −1200 / 2390 ms (n=49) | +1200 ms → 340 / 1190 ms | +120 ms/min | 331 / 989 ms |

Leitura: Rita Lee começa 1,8 s adiantada e termina 1 s atrasada — a sincronia do LRCLIB foi feita sobre outra
versão/andamento (ou batida à mão sem cuidado); Ara Ketu é quase só deslocamento (intro/master diferente); Perfect é
"boa", mas o tremor de ±0,5 s é perceptível no karaokê palavra a palavra.

## Números — modo `aligned` (AC-08.6, meta ≤ 150 ms)
| Música | `aligned` − Whisper (médio / máx) | `aligned` − ataque de voz (médio / mediana / máx) | LRC − ataque de voz (médio) | Linhas ancoradas | Tempo do alinhamento |
|---|---|---|---|---|---|
| Minha vida | 0 / 0 ms | **22** / 0 / 110 ms (n=20) | 539 ms | 21/21 | 60 ms |
| Perfect | 0 / 0 ms | **115** / 0 / 830 ms (n=45) | 132 ms | 45/45 | 190 ms |
| Sempre Será | 0 / 0 ms | **140** / 0 / 955 ms (n=52) | 426 ms | 52/55 (3 interpoladas) | 120 ms |

- Meta atendida nas 3 (média ≤ 150 ms, mediana 0). Os máximos vêm de poucas linhas que começam com vogal longa
  / ligadas à anterior, onde não há "ataque" nítido no envelope (a referência independente erra, não só o alinhamento).
- Tempo total "alinhar uma música de 4 min" na GPU = transcrição Whisper (dominante; o NFR de ≤ 30 s é verificado pelo
  teste real abaixo) + alinhamento (< 0,2 s de CPU).
- Transcrições usadas: geradas em 2026-10-04 17:44 (GPU) pela execução anterior desta tarefa, com o mesmo
  `transcribe.run` do código atual; números recalculados com o `align.py` atual.

## Correções feitas a partir do diagnóstico
- Nenhum bug de parse/offset a corrigir. Ajustes no alinhamento descobertos aqui (com teste em `tests/unit/test_align.py`):
  correção do início de palavra do Whisper que absorve silêncio (envelope do vocal), desempate de refrões repetidos
  por penalidade de tempo, linhas não cantadas interpoladas e marcadas `interpolated: true`.

## Comandos para reproduzir
```sh
cd projects/karaoke
cp -r --reflink=auto data /tmp/karaoke-copia            # nunca rode sobre data/
# com transcrição nova (GPU, ~10–20 s por música; grava só em /tmp/sync-report):
.venv/bin/python -m karaoke.tools.lyrics_sync_report --data /tmp/karaoke-copia --out /tmp/sync-report
# reaproveitando transcrições já salvas (<id>.whisper.json + <id>.env.json):
.venv/bin/python -m karaoke.tools.lyrics_sync_report --data /tmp/karaoke-copia --transcripts <pasta> --no-transcribe
```
Saída: um bloco por música com as linhas "LRC − voz", "após offset constante", "deriva", "ALIGNED − voz (Whisper)",
"ALIGNED − ataque de voz" e "meta AC-08.6: OK/FALHOU"; JSON completo em `<out>/report.json`.
