# ADR-0005 — Provedores de letra adicionais (Vagalume, lyrics.ovh)

- Status: proposto
- Data: 2026-10-20
- Autor: architect · Ciclo: `20261020-2304-karaoke` · Histórias: US-22, US-23, US-24

## Contexto

Hoje a letra oficial vem só do LRCLIB (`karaoke/lrclib.py`, cadeia `find_candidates` → `rank`/`acceptable`).
Sem candidata, o pipeline usa a transcrição do Whisper. Desde o CR-001 já existe o caminho "texto oficial sem
tempos → sincronizar pela voz" (`online_plain`, `TEXT_SOURCES`, `text_base_order`, `try_align`). O ciclo
acrescenta fontes secundárias de **texto puro**, consultadas depois do LRCLIB, no pipeline e na busca manual.

Fontes verificadas em 2026-10-20 (curl):

| Fonte                                                            | Acesso                                   | Formato observado                                                                                                                                                                                                                                                                         |
| ---------------------------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Vagalume API `api.vagalume.com.br/search.php?art=&mus=&apikey=`  | só com chave (sem chave → 503)           | JSON `{type, art:{name}, mus:[{name, text}]}`                                                                                                                                                                                                                                             |
| Vagalume página `www.vagalume.com.br/<art-slug>/<mus-slug>.html` | público, 200; slug inexistente → **404** | `<div id=lyrics data-plugin=googleTranslate>…<br>…</div>` (atributos sem aspas, 1 ocorrência); título em `<h1>Canção Exemplo</h1>`; artista em `<h1 class="darkBG long"><a href=/banda-ficticia/ …>Banda Fictícia</a>`; `og:title` = `"<Música> - <Artista> - VAGALUME"` (entidades HTML) |
| Vagalume busca pública (`/api/search.php`, `search.excerpt`)     | 404/503                                  | inutilizável sem chave                                                                                                                                                                                                                                                                    |
| lyrics.ovh `api.lyrics.ovh/v1/<artista>/<faixa>`                 | público                                  | `{"lyrics": "…"}`; ausente → **404** `{"error":"No lyrics found"}`; pode conter marcadores `[Vocalista]`                                                                                                                                                                                  |
| lyrics.ovh `api.lyrics.ovh/suggest/<termo>`                      | público                                  | JSON Deezer `{data:[{id, title, duration (s), artist:{name}}]}`                                                                                                                                                                                                                           |
| Letras.mus.br                                                    | 403                                      | fora                                                                                                                                                                                                                                                                                      |

Ordem decidida pelo usuário: LRCLIB → Vagalume (API se houver chave, senão página) → lyrics.ovh; automático no
pipeline **e** na busca manual.

## Alternativas consideradas

| Critério (peso)                                   | A. Fontes dentro de `lrclib.py` | **B. Pacote `lyrics_providers/` + cadeia única** | C. Novo `source` por provedor (`vagalume`, `lyricsovh`)                                                                         |
| ------------------------------------------------- | ------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| Isolamento/testabilidade (3)                      | 1 — módulo vira um balaio       | 3 — cada provedor com `MockTransport` próprio    | 3                                                                                                                               |
| Mudança em `TEXT_SOURCES`/seleção/alinhamento (3) | 3                               | 3 — reutiliza `online_plain`                     | 1 — toca `TEXT_SOURCES` (2 cópias), `text_base_order`, `_text_base`, `candidate_summary`, SQL `IN (...)` em 3 lugares, frontend |
| Um ponto de orquestração (2)                      | 2                               | 3                                                | 3                                                                                                                               |
| Esforço (2)                                       | 3                               | 2                                                | 1                                                                                                                               |
| **Total**                                         | 21                              | **28**                                           | 19                                                                                                                              |

Para o parsing do HTML do Vagalume: (i) `html.parser` da stdlib + regex delimitada, (ii) BeautifulSoup/lxml
(dependência nova). Escolhido (i): o trecho é um `div` único sem aninhamento; uma dependência não se paga.

## Decisão

### 1. Interface de provedor e onde fica a cadeia

Novo pacote `karaoke/lyrics_providers/` (o LRCLIB **continua** em `karaoke/lrclib.py`, sem mudança de API):

```
karaoke/lyrics_providers/
  __init__.py     # PlainLyrics, Provider (Protocol), ProviderNetworkError, build_providers()
  http.py         # make_client(transport=None) -> httpx.Client (UA, timeouts, sem retry)
  vagalume.py     # VagalumeProvider(api_key: str | None, transport=None)
  lyricsovh.py    # LyricsOvhProvider(transport=None)
  chain.py        # search_lyrics(...) -> ChainResult; plain_candidate(p) -> dict
```

```python
@dataclass(frozen=True)
class PlainLyrics:
    provider: str  # "vagalume" | "lyricsovh"
    provider_id: str  # "vagalume:<art-slug>/<mus-slug>" | "lyricsovh:<deezer_id>" (string: nunca colide com o int do LRCLIB)
    track_name: str  # nome que a FONTE devolveu (não o pedido)
    artist_name: str
    duration_ms: int | None  # lyrics.ovh: duração do Deezer (suggest); Vagalume: None
    text_lines: list[str]  # já limpas (ver §1.1); vazio = não é resultado


class ProviderNetworkError(Exception):
    kind: Literal["offline", "timeout"]  # offline = httpx.ConnectError; timeout = httpx.TimeoutException


class Provider(Protocol):
    name: str  # "vagalume" | "lyricsovh"

    def search(self, track: str, artist: str | None) -> list[PlainLyrics]: ...  # 0 ou 1 item nesta versão
    def close(self) -> None: ...
```

`search` **só levanta** `ProviderNetworkError`; HTTP ≠ 200, JSON/HTML inesperado, nome que não bate → `[]`
(com `log.info("<provider>: <motivo curto>")`). `build_providers(settings, transport=None) -> list[Provider]`
devolve `[]` se `extra_lyrics_sources` é false, senão `[VagalumeProvider(key), LyricsOvhProvider()]` nesta
ordem. Testes substituem via `app.state.lyrics_providers_factory: Callable[[dict], list[Provider]]` (mesmo
padrão de `lrclib_factory`).

**Ponto único de orquestração** — `chain.search_lyrics`:

```python
@dataclass
class ChainResult:
    records: list[lrclib.LrcRecord]       # ranqueados (find_candidates), como hoje
    extra: list[PlainLyrics]              # na ordem dos provedores, já deduplicados (§3)
    status: dict[str, str]                # {"lrclib"|"vagalume"|"lyricsovh": "ok"|"empty"|"error"|"offline"|"timeout"|"skipped"}
    lrclib_error: lrclib.LrclibNetworkError | None

def search_lyrics(client, providers, track, artist, duration_ms, raw_title, *, exhaustive: bool) -> ChainResult
```

Usado por `JobRunner._official_candidates` (pipeline e job `align` sem texto guardado; `exhaustive=False`) e
por `LyricsService.search` (busca manual; `exhaustive=True`). Ambos continuam convertendo `records` com
`lrclib_candidate` e passam a converter `extra` com `chain.plain_candidate(p)`, que devolve o mesmo dicionário
de candidata que `lrclib_candidate` gera para só-texto:
`{"source": "online_plain", "timing": "none", "lines": [], "text_lines": p.text_lines, "language": None,
"meta": {track_name, artist_name, album_name: None, provider_id, provider, duration_ms, model: None}}`.
`lrclib_candidate` passa a gravar `meta.provider = "lrclib"` (candidatas antigas sem o campo = lrclib).
`aligned_candidate` copia `meta.provider` da base.

#### 1.1 Provedores

**HTTP comum** (`http.make_client`): `User-Agent` = `lrclib.USER_AGENT`; `httpx.Timeout(5.0, connect=3.0)`;
`follow_redirects=True`; **nenhuma** nova tentativa (nem em 5xx/timeout); 1 cliente por chamada da cadeia.
Pior caso por provedor: 2 requisições ≤ ~10 s (SPEC §3).

**Vagalume**

1. Com chave: `GET https://api.vagalume.com.br/search.php?art=<artista>&mus=<faixa>&apikey=<chave>`. Aceita se
   200, JSON com `type in ("exact","aprox")` e `mus[0].text` não vazio; nomes = `art.name`/`mus[0].name`;
   `provider_id = "vagalume:api:<mus[0].id>"`. Qualquer outra resposta (401/403/503, `type` `notfound`/
   `song_notfound`, JSON inválido) → passa para a página (passo 2). `ProviderNetworkError(offline)` não passa.
2. Página: `slug(s)` = `normalize` sem acento/pontuação (`titleclean.normalize`), espaços → `-`
   (`"Banda Fictícia"` → `banda-ficticia`, `"Canção Exemplo"` → `cancao-exemplo`); `&` vira `e` antes da
   normalização. Sem artista → `[]` (não há busca pública).
   `GET https://www.vagalume.com.br/<art>/<mus>.html`; 404 → `[]`. Extração com `html.parser.HTMLParser`
   (stdlib): conteúdo do primeiro `<div id="lyrics">` (aceitar atributo com ou sem aspas) até o `</div>`
   correspondente; `<br>` → `\n`; demais tags descartadas; entidades via `html.unescape`. Nomes: texto do
   primeiro `<h1>` sem filhos (faixa) e do `<a>` dentro do `<h1 class~="darkBG">` (artista); se faltar algum,
   `og:title` dividido em `" - "` (`[faixa, artista, "VAGALUME"]`). Nada encontrado ou < 2 linhas → `[]`
   ("HTML mudou"). `provider_id = "vagalume:<art-slug>/<mus-slug>"`.

**lyrics.ovh**

1. `GET https://api.lyrics.ovh/suggest/<artista> <faixa>` (URL-encoded). Escolhe o 1º item de `data` que
   **bate no nome** (§3). Nenhum → `[]` (sem o suggest não há como verificar a correspondência — AC-22.3).
2. `GET https://api.lyrics.ovh/v1/<artist.name>/<title>` (nomes canônicos do item, URL-encoded por segmento).
   200 com `lyrics` não vazio → resultado; 404 → `[]`. `duration_ms = item.duration * 1000`,
   `provider_id = "lyricsovh:<item.id>"`.

**Limpeza do texto** (ambos, antes de `builder.text_lines_of`): descartar 1ª linha que começa com
`"Paroles de la chanson"`; descartar linhas inteiras entre colchetes (`^\[[^\]]*\]$`, ex.: `[Refrão]`,
`[Vocalista]`); `text_lines_of` cuida do resto (vazias, tags LRC, 500 chars).

### 2. Representação da candidata

**`source = "online_plain"` + `meta.provider`** (alternativa B). `TEXT_SOURCES`, `text_base_order`,
`_text_base`, `select`, `align` e os SQL `source IN ('lrclib','online_plain')` ficam **inalterados**. Id da
candidata segue `online_plain:<n>`. O resumo (`candidate_summary`) ganha:

- campo `provider`: `"lrclib" | "vagalume" | "lyricsovh"` em `lrclib`, `online_plain` e `aligned` (ausente no
  meta → `"lrclib"`); `null` em `whisper`/`manual`;
- rótulo por provedor quando `source == "online_plain"`:

| provider    | `label`                           |
| ----------- | --------------------------------- |
| `lrclib`    | `"Online · só texto"` (como hoje) |
| `vagalume`  | `"Vagalume · só texto"`           |
| `lyricsovh` | `"lyrics.ovh · só texto"`         |

Os demais rótulos não mudam (`aligned` derivada de Vagalume continua "Online · sincronizada pela voz"; a
origem fica em `provider`). Implementação: `PROVIDER_PLAIN_LABELS` em `services/lyrics.py`.

### 3. Correspondência de nome e deduplicação

`match(cand_track, cand_artist, track, artist)` em `chain.py`, com `n = titleclean.normalize` e
`s = titleclean.strip_noise`:

- **faixa**: `n(s(cand_track)) == n(s(track))`;
- **artista** (só se `artist` informado): `n(cand_artist) == n(artist)` **ou** um contém o outro como
  sequência de palavras inteiras (`"banda ficticia"` ⊂ `"banda ficticia e convidados"`). Sem artista: só a
  faixa. Falha → descartado (AC-22.3). Vale para os dois provedores (Vagalume API, página e lyrics.ovh
  suggest). O LRCLIB mantém o ranking atual.

**Deduplicação** (na cadeia e antes de gravar):

1. `provider_id` já presente nas candidatas `lrclib`/`online_plain` da música (`known`, como hoje — agora o
   conjunto mistura ints do LRCLIB e strings das fontes extras) → descartado;
2. **texto igual**: impressão = `n(" ".join(text_lines[:3]))`; se igual à de outra candidata desta busca
   (inclusive registros LRCLIB) ou ao `meta.preview` de uma candidata online/colada já gravada → descartado.

### 4. Regra de disparo

- **Pipeline** (`exhaustive=False`): LRCLIB como hoje. Se **algum** registro LRCLIB é `lrclib.acceptable`
  (duração ≤ 3 s, com tempos **ou** só texto) → fontes extras `skipped` (AC-22.2). Senão, provedores em ordem,
  **parando no primeiro** com resultado. Se o LRCLIB falhou com `LrclibNetworkError` que **não** é
  `LrclibUnavailableError` (sem conexão) → todas `skipped`. `offline` num provedor → os seguintes `skipped`;
  `timeout`/`error` → segue para o próximo. As candidatas extras entram em `cands` e seguem o caminho de
  `online_plain` (`text_base_order` grupo 1 se lyrics.ovh trouxe duração aceitável, senão grupo 2 pelo nome →
  `try_align`); sem alinhamento suficiente → Whisper, como hoje. `extra_lyrics_sources=false` →
  `providers=[]`, comportamento idêntico ao atual (AC-22.5).
- **Busca manual** (`exhaustive=True`): sempre LRCLIB **e** todos os provedores habilitados, cada um
  independente (falha de um não impede os outros, nem `offline` no LRCLIB). Resultados: LRCLIB primeiro,
  depois Vagalume, depois lyrics.ovh, deduplicados (AC-23.1). Não troca a seleção.
- Termos: pipeline usa `song.title` e `song.artist or clean_channel(channel)` (como hoje); busca manual usa os
  termos editados (ou título/artista atuais).

### 5. Erros

| Situação                                                                     | Status do provedor | Efeito                                      |
| ---------------------------------------------------------------------------- | ------------------ | ------------------------------------------- |
| `httpx.ConnectError`                                                         | `offline`          | sem nova tentativa; pipeline: para a cadeia |
| `httpx.TimeoutException` (connect 3 s / read 5 s)                            | `timeout`          | sem nova tentativa                          |
| HTTP ≠ 200 (exceto o fallback API→página), JSON/HTML inválido, nome não bate | `error` / `empty`  | `[]`                                        |
| Outros `httpx.HTTPError`                                                     | `offline`          | idem ConnectError                           |

Nunca vira erro de job (AC-22.4). **Busca manual**: `502 network_unavailable` **só** se todas as fontes
consultadas falharam por rede (`offline`/`timeout` em todas; LRCLIB conta como rede quando levanta
`LrclibNetworkError`) — AC-23.3. Mensagem: com fontes extras ligadas
`"Não foi possível falar com LRCLIB, Vagalume e lyrics.ovh. A busca de letras exige internet."`,
`details.reason = "all_sources_unavailable"`; desligadas → mensagens atuais (`network_unavailable` /
`lrclib_unavailable`) sem mudança. Algum provedor respondeu (mesmo vazio) → `200`.

Logs: só nome do provedor + status HTTP ou classe da exceção. **Nunca** a URL da API do Vagalume (contém a
chave); o `httpx` loga URLs em INFO, então o backend fixa
`logging.getLogger("httpx").setLevel(logging.WARNING)` (se ainda não estiver) e há teste com `caplog`
garantindo que a chave não aparece.

### 6. Configurações

- `extra_lyrics_sources: bool`, padrão `true`, em `default_settings`/`SettingsPatch`.
- `vagalume_api_key`: gravada na tabela `settings` (chave `vagalume_api_key`, JSON string), **fora** de
  `default_settings`, portanto nunca sai em `GET /api/settings`, `settings.updated` nem no `hello` do SSE.
  `read_settings` acrescenta `vagalume_api_key_set: bool`. `SettingsPatch.vagalume_api_key: str | None`
  (`max_length=200`): string após `strip()` não vazia → grava; `""` ou `null` → apaga a linha.
  `vagalume_api_key_set` no PATCH → `400 invalid_request` (`extra="forbid"`, padrão da app — DECISION 0007). A
  chave nunca aparece em logs/erros.
- Lida no momento da busca (`build_providers(settings)`), sem reinício.

### 7. Parsing sem dependência nova

`html.parser.HTMLParser` + `html.unescape` + `re` da stdlib (§1.1). Sem BeautifulSoup/lxml.

### 8. Testes (sem rede)

- `tests/fixtures/lyrics_providers/`: `vagalume_page.html` (recorte pequeno real: `<h1>`s, `og:title`,
  `div#lyrics` com `<br>`, entidades), `vagalume_page_changed.html` (sem `div#lyrics`),
  `vagalume_api_exact.json`, `vagalume_api_notfound.json`, `lyricsovh_suggest.json`, `lyricsovh_lyrics.json`
  (com `[Refrão]`), `lyricsovh_404.json`.
- `tests/unit/test_lyrics_providers.py`: cada provedor com `httpx.MockTransport` — sucesso, 404, HTML mudou,
  nome divergente (AC-22.3), `ConnectError` → `offline` sem 2ª requisição (contar chamadas), timeout, API com
  chave → 403 → página, slug com acento/`&`, limpeza de marcadores.
- `tests/unit/test_lyrics_chain.py`: disparo (LRCLIB aceitável → extras `skipped`; LRCLIB offline → `skipped`;
  para no 1º resultado; `exhaustive` consulta todos), dedupe por `provider_id` e por texto.
- `tests/jobs`/`tests/unit/test_runner.py`: pipeline sem LRCLIB aceitável usa texto do Vagalume como base do
  alinhamento; provedor com erro → Whisper; fontes desligadas → zero chamadas aos provedores.
- `tests/api`: `/lyrics/search` com rótulos/`provider`/`sources`; 502 só com todas em rede;
  `test_settings.py`: `extra_lyrics_sources`, chave grava/remove, nunca devolvida, `caplog` sem a chave.

## Consequências

- (+) Nenhuma mudança no fluxo de seleção/alinhamento; o frontend já usa `label` do backend.
- (+) Novo provedor = um arquivo + uma linha em `build_providers`.
- (−) Scraping do Vagalume quebra se o HTML mudar → degrada para lyrics.ovh/Whisper (teste com fixture
  alerta).
- (−) `known` mistura `int` e `str`; comparação continua por igualdade (sem colisão por tipo).
- (−) Pipeline pode ganhar até ~20 s no pior caso (dois provedores lentos) só quando o LRCLIB não tem letra.
- Reversível: `extra_lyrics_sources=false` restaura o comportamento anterior.
