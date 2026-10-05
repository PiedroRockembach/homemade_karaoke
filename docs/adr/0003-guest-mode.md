# ADR-0003 — Modo convidado (LAN + QR): rede, convite, sessão e limites

- Status: aceito
- Data: 2026-10-05
- Autor: architect · Ciclo: `20261005-0109-karaoke` · Tarefa: T-001 · Histórias: US-11..US-16
- Substitui parcialmente: [ADR-0002](0002-guest-mode-readiness.md) (§1 "LAN como opt-in" e os itens marcados "futuro").
  O modelo de papéis, o ponto único de autorização e o deny-by-default da ADR-0002 continuam valendo.
- Relacionados: [api.md §0.1, §12](../contracts/api.md), [ARCHITECTURE.md §11](../ARCHITECTURE.md)

## Contexto

O anfitrião liga o "Modo convidado" no Controle, **sem reiniciar** o app (AC-11.2), e a cada reinício o modo volta a
desligado (AC-11.5). Com o modo desligado, **nenhuma rota pode responder a clientes que não sejam loopback** (SPEC §3,
AC-11.1), e o constraint do estúdio é "escutar só em 127.0.0.1 por padrão". O QR na TV leva a
`http://<ip-lan>:<porta>/guest#t=<token>`. Os convidados (2 a ~20 celulares) buscam, pedem e acompanham a fila com o
próprio nome, sob limites por convidado.

Ambiente verificado em 2026-10-05:
- Python 3.12 (`.venv`), FastAPI 0.142.2, Starlette 1.7.0, uvicorn 0.54.0.
- `uvicorn.Server.serve(sockets=[...])` aceita sockets já abertos, e `serve()` envolve tudo em `capture_signals()`,
  que **troca os handlers de SIGINT/SIGTERM** quando roda na thread principal (ver decisão 1).
- A máquina do usuário tem `lo`, `enp6s0` (192.168.1.16), `wlan0` (192.168.1.4, mesma sub-rede) e `docker0`
  (172.17.0.1, DOWN). Ou seja, o caso "várias interfaces" já é real.
- No PyPI: `segno` 1.6.6 e `qrcode` 8.2. Nenhum dos dois está instalado.
- Lição da v0.2 (FIX-002): streams SSE abertos impedem o shutdown gracioso do uvicorn. Eles precisam ser fechados
  antes de o servidor esperar as conexões.

---

## Decisão 1 — Rede: um 2º listener dinâmico no IP da LAN

### Opções
- **A. Bind dinâmico.** O servidor principal fica sempre em `127.0.0.1:<porta>`. Ao ligar o modo, sobe um 2º
  `uvicorn.Server` no **mesmo processo e no mesmo loop**, com o **mesmo app**, ouvindo só em `<ip-lan>:<porta>`. Ao
  desligar, esse servidor é parado.
- **B. Bind permanente em `0.0.0.0`.** Com o modo desligado, toda conexão não-loopback é recusada no `authorize` (ou
  num middleware ASGI que fecha a conexão).
- **C. Reiniciar o servidor principal** com outro host ao ligar o toggle, ainda dentro do mesmo processo.

### Matriz (1–5, peso entre parênteses)
| Critério | A | B | C |
|---|---|---|---|
| Superfície de rede com o modo desligado (×3) | 5 (= v0.2) | 1 (porta aberta em todas as interfaces, parser HTTP exposto) | 5 |
| Aderência ao constraint "127.0.0.1 por padrão" (×3) | 5 | 1 | 4 |
| Simplicidade / poucas peças (×2) | 3 | 5 | 2 |
| Robustez de start/stop e shutdown (×2) | 3 | 4 | 1 (derruba o lifespan, mata o worker de IA, corta o SSE do Palco) |
| Firewall e escolha de interface (×1) | 5 (só o IP escolhido; docker/VPN nunca expostos) | 2 (expõe docker/VPN; firewall vê a porta sempre) | 4 |
| **Total ponderado** | **47** | **26** | **37** |

### Decisão: A
Módulo `karaoke/guest/listener.py` (`LanListener`):
- **Socket criado por nós**: `socket(AF_INET)`, `SO_REUSEADDR`, `bind((ip, porta))` e `listen(128)`. Erros de bind
  aparecem **na hora**, de forma síncrona, como `409 lan_bind_failed`. Só depois disso o servidor é iniciado com
  `server.serve(sockets=[sock])` como `asyncio.Task` no loop do app. O `enable` espera `server.started` (até 2 s).
- **`uvicorn.Config` do listener**: `lifespan="off"` (o lifespan já rodou no principal; os serviços não podem iniciar
  duas vezes), `proxy_headers=False` (obrigatório: sem isso, um `X-Forwarded-For: 127.0.0.1` vindo da LAN poderia
  se passar por loopback), `server_header=False`, `log_config=None` (não reconfigura o logging),
  `timeout_graceful_shutdown=1`, `log_level="warning"`.
- **Conexões (FIX-001, review-1 M-3/H-1)**: `limit_concurrency=64` (30 SSE de convidado + folga → `503` acima disso),
  `timeout_keep_alive=3`, `backlog=64` e o protocolo `LanH11Protocol` (fecha na hora conexões acima de 96 e as que
  não completam a 1ª requisição em 10 s — slowloris); corpo > 16 KiB → `413 payload_too_large` antes de qualquer
  leitura (`karaoke/bodylimit.py`, nos dois servidores; 8 MiB no loopback). Risco residual aceito: um aparelho da LAN
  ainda pode ocupar as 64 vagas e negar serviço **aos convidados** (o host, no loopback, não é afetado).
- **Sinais**: a subclasse `LanServer(uvicorn.Server)` sobrescreve `capture_signals()` com um `nullcontext`. Sem isso,
  o 2º servidor tomaria o SIGINT/SIGTERM do `KaraokeServer`, e o Ctrl+C só pararia o listener da LAN.
- **Porta**: a mesma porta efetiva do loopback (`config.port`). Não há fallback: se a porta estiver ocupada no IP da
  LAN, o erro é `409 lan_bind_failed`. Assim a URL fica previsível e as regras de firewall valem para uma porta só.
- **Parar** (desligar o modo, trocar de IP ou encerrar o app), em `LanListener.stop()`, nesta ordem:
  1. o `GuestService` marca o modo como desligado, o que faz o `authorize` recusar convidados na hora;
  2. envia `guest.ended {reason}` a cada assinatura SSE `role=guest` e chama `bus.close_role("guest")`, para que os
     streams terminem (lição do FIX-002);
  3. faz `server.should_exit = True` e aguarda a task por até 3 s; se passar disso, `force_exit = True` e `task.cancel()`.
- **Encerramento do app**: `KaraokeServer.begin_shutdown()` (1º sinal) também chama
  `app.state.guest.listener.request_stop()` (síncrono, sem lock: só marca `should_exit`). O `shutdown(app)` do
  `GuestService`, no lifespan, aguarda o fim. O `hard_exit` continua cobrindo o pior caso.
- **Identidade da conexão**: o papel `guest` só é aceito em requisições que chegaram pelo listener da LAN
  (`request.scope["server"][0] == ip escolhido`) e com o cliente fora do loopback. O papel `host` continua sendo só
  "cliente loopback" (ADR-0002). Um navegador no próprio PC que abra `http://192.168.1.16:8765/guest` tem IP de
  origem 192.168.1.16, então é tratado como convidado. Isso é útil para testar sem celular.

**Com o modo desligado, a superfície é idêntica à da v0.2**: nada escuta fora do loopback, e o celular recebe
*conexão recusada* (AC-11.1 aceita isso explicitamente).

**AC-11.4 ao desligar o modo:** o listener deixa de existir, então as requisições seguintes falham com conexão
recusada, e não com 401. A página mostra "Sessão encerrada" porque recebe `guest.ended {reason:"disabled"}` pelo SSE
antes do fechamento. Se a conexão cair sem esse evento, a página tenta 3 vezes e mostra a mesma tela com a dica
"o anfitrião desligou o modo convidado". Com **"Encerrar convidados"** o listener continua de pé, e as próximas
requisições recebem `401 guest_session_required`, como pede o AC.

## Decisão 2 — Descoberta do IP da LAN

Só biblioteca padrão, Linux (o SO-alvo), em `karaoke/guest/netinfo.py`:
1. Enumera as interfaces com `socket.if_nameindex()`. Para cada uma, lê o IPv4 primário com
   `fcntl.ioctl(SIOCGIFADDR)` e as flags com `SIOCGIFFLAGS` (`IFF_UP`, `IFF_LOOPBACK`, `IFF_POINTOPOINT`).
2. Classifica cada endereço:
   - `loopback` e link-local `169.254/16`: descartados;
   - `virtual`: existe `/sys/devices/virtual/net/<if>` ou o nome começa com `docker`, `br-`, `veth`, `virbr`, `lxc`,
     `podman`, `cni`, `flannel`, `vmnet` ou `vboxnet`. É **descartado**;
   - `vpn`: tem `IFF_POINTOPOINT`, o nome começa com `tun`, `tap`, `wg`, `tailscale`, `zt`, `ppp` ou `nordlynx`, ou o
     endereço está em `100.64/10`. É listado, mas nunca recomendado;
   - `wifi`: existe `/sys/class/net/<if>/wireless`;
   - `ethernet`: o resto (RFC 1918 privado). Um IP público entra como `other`.
3. **Recomendado**: o endereço de origem da rota padrão. Ele é obtido com um `socket(AF_INET, SOCK_DGRAM)` e
   `connect(("192.0.2.1", 9))`, que não envia nenhum pacote; se falhar (offline, sem rota), usa a ordem a seguir. Só
   vale se o endereço for `ethernet` ou `wifi`; senão vale a ordem `ethernet` > `wifi` > `other`.
4. **Só IPv4**: endereços IPv6 deixam a URL do QR longa e mudam com o SLAAC.
5. O painel recebe `addresses[]` com `{address, interface, kind, recommended}`. Com mais de uma opção ele mostra um
   seletor. O `POST /api/guest/enable {address}` só aceita um endereço que esteja na lista atual (senão `400`). Sem
   candidatos → `409 lan_unavailable` ("Conecte o PC ao Wi-Fi ou cabo da sua rede"). A escolha fica só em memória.
6. **Dica de firewall**: `shutil.which("ufw")` ou `which("firewall-cmd")` gera `firewall_hint` com o comando pronto
   (`sudo ufw allow <porta>/tcp` · `sudo firewall-cmd --add-port=<porta>/tcp`). Não tentamos detectar se o firewall
   está ativo, porque isso exige root.

## Decisão 3 — QR em SVG: `segno`

| Critério (peso) | `segno` 1.6.6 | `qrcode` 8.2 | Implementação própria |
|---|---|---|---|
| Sem dependências transitivas (×3) | 5 (zero) | 4 (zero no núcleo; extras opcionais) | 5 |
| SVG nativo, compacto, com `viewBox` (×2) | 5 | 3 (factory de SVG via `xml.etree`, saída maior) | 3 |
| Risco de bug / manutenção (×3) | 5 (madura, conforme a ISO 18004) | 5 | 1 (Reed-Solomon e máscaras: cerca de 400 linhas para testar) |
| Tamanho (×1) | 5 (puro Python, cerca de 100 KB) | 4 | 5 |
| **Total** | **45** | **37** | **29** |

**Decisão:** `segno~=1.6.6` em `pyproject.toml` (BSD-3, puro Python, sem dependências). Geração em
`karaoke/guest/qr.py`: `segno.make(url, error="m", micro=False)` e depois
`qr.save(buf, kind="svg", xmldecl=False, svgns=True, omitsize=True, border=4, dark="#000", light="#fff")`.
Fica preto no branco **nos dois temas** (leitores de QR exigem esse contraste) e com zona de silêncio de 4 módulos.
O tamanho na tela vem do CSS do Palco, graças ao `viewBox`. A URL tem cerca de 57 caracteres, o que dá versão 4–5.
O SVG fica em cache por versão do convite.

## Decisão 4 — Convite, sessão e `authorize`

**Convite** (`GuestInvite`, só em memória, um ativo):
- token = `secrets.token_urlsafe(16)` (128 bits, 22 caracteres); em memória fica só `sha256(token)` e, além dele,
  **a URL pronta para o QR**. A URL precisa existir enquanto o modo estiver ligado, porque o Palco a exibe. Ela só sai
  por rotas `host` e pelo evento `guest.state`, que só chega a conexões `host`.
- validade de 12 h; **expirou com o modo ligado → rotação automática** (verificada a cada 60 s), então o QR da TV
  continua válido. `version` cresce a cada rotação e invalida o cache do QR.
- "Novo QR" (`rotate`) troca o token e mantém as sessões (AC-11.3). "Encerrar convidados" (`end-sessions`)
  invalida todas as sessões **e rotaciona** o convite: sem rotação, quem tivesse o link antigo voltaria na hora.
  Desligar o modo apaga o convite e as sessões.
- A comparação é feita com `hmac.compare_digest` sobre os hashes.

**Sessão** (`GuestSession`, só em memória):
- `POST /api/guest/session {token, name}` cria a sessão. O id é `g_<n>`, com `n` vindo do contador **persistente**
  `counters('guest')`. Assim o id nunca se repete entre execuções, e um item antigo da fila (com `added_by="g_3"`)
  não passa a pertencer a uma nova sessão `g_3` depois de um reinício.
- O cookie é `karaoke_guest=<secrets.token_urlsafe(32)>; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200`, sem
  `Secure` porque é HTTP na LAN (risco aceito na ADR-0002). Em memória fica `sha256(valor) → sessão`.
- A sessão vale no máximo 12 h desde a criação e não depende da rotação do convite. Há no máximo 50 sessões ativas;
  acima disso → `429 rate_limited {reason:"too_many_guests"}`.
- Nome: aplica NFC, remove caracteres de controle e de formatação (categorias Unicode `Cc`/`Cf`, o que inclui os
  overrides bidi), junta espaços repetidos, tira `strip()` e exige 1–40 caracteres. Senão
  `400 invalid_request {fields:{name}}`. Os nomes não precisam ser únicos.
- O `SameSite=Strict` não atrapalha: a navegação vinda da câmera não envia o cookie, mas `/guest` é pública, e os
  `fetch`/`EventSource` que vêm depois, na mesma origem, enviam.

**Novo escopo `public`** e o papel `anon`. A página `/guest`, `/static/*` e a rota de criar e ler a sessão precisam
responder **antes** de existir sessão. Escopos: `host` · `guest-safe` · `public`. O deny-by-default continua: sem
marcador, a rota vale como `host`.

**`authorize` v0.3** (`karaoke/security.py`):
```
1. Host ∈ config.allowed_hosts ∪ guest.allowed_hosts()          senão 403 forbidden
      guest.allowed_hosts() = {"<ip-lan>:<porta>"} só com o modo ligado; vazio se desligado
2. Mutação: Origin ausente ou ∈ origens correspondentes;
            corpo ⇒ Content-Type application/json                senão 403 / 400
3. Principal:
     cliente loopback                                   → Principal(host)
     modo desligado ou conexão fora do listener da LAN  → 403 forbidden (guest_mode_off)
     cookie karaoke_guest válido                        → Principal(guest, g_<n>, nome)  [atualiza last_seen]
     senão                                              → Principal(anon, "anon")
4. Escopo × papel:
     host       exige host                               senão 403 forbidden (host_only)
     guest-safe exige host|guest;  anon                  → 401 guest_session_required
     public     qualquer papel
5. RateLimiter.check(principal, request)                 → 429 rate_limited {retry_after_s}
```
Nenhum handler compara papéis. Restrições que dependem de dados (cotas, posse) ficam em `karaoke/guest/policy.py`,
chamadas pelos handlers. Essas funções não fazem nada quando o principal é `host`.

## Decisão 5 — Limites

**Rate limiter real**: token bucket em memória (`karaoke/guest/ratelimit.py`, implementa o `RateLimiter` da ADR-0002;
`app.state.rate_limiter`). A chave é `(id do convidado | IP do anon, balde)`. O host nunca é limitado.

| Balde | Aplica a | Capacidade | Recarga | Ao estourar |
|---|---|---|---|---|
| `search` | `GET /api/search` (YouTube) | 10 | 10/min (1 a cada 6 s) | `429 rate_limited` "Calma! Tente em alguns segundos" (AC-14.5) |
| `write` | demais mutações do convidado | 10 | 20/min | `429` |
| `read` | demais GETs do convidado (inclui thumbs e `GET /api/songs`) | 60 | 120/min | `429` |
| `session` | `POST /api/guest/session` (por IP) | 5 | 10/min | `429` (contra força bruta, além dos 128 bits) |
| `anon` | rotas `public` sem sessão (por IP) | 60 | 120/min | `429` |

`details.retry_after_s` = segundos até o próximo token (inteiro ≥ 1); o header `Retry-After` vem igual.

**Cotas por convidado** (dependem do banco; ficam em `policy.py`, não no limiter):
| Cota | Valor | Conta | Erro |
|---|---|---|---|
| itens pendentes na fila | 3 | `queue_items` com `added_by = g_<n>` e `status = pending` (prontos ou não) | `409 guest_quota_exceeded {quota:"pending_items", limit:3}` |
| preparos ativos | 2 | `jobs` com `requested_by = g_<n>` em `queued`/em execução | `409 guest_quota_exceeded {quota:"active_jobs", limit:2}` |
| duração do vídeo | ≤ `settings.long_video_warn_s` (900 s) | a duração **apurada pelo servidor** (abaixo) | `409 long_video_not_allowed {duration_ms, max_ms}` |

**DECISION 0016 (2026-10-05, FIX-002):** removidas as cotas `pending_items`/`active_jobs` e os limites de sessão por IP do FIX-001 (uso por ≤ 10 pessoas de confiança; qa-2 N-1); ficam a duração, os baldes (o `session` passou a 10, 20/min), `too_many_guests` (50 ativas), o limite de corpo e o de conexões.

A duração não vem do corpo da requisição do convidado. O `POST /api/jobs` de convidado ignora `title`, `channel`,
`duration_ms`, `confirm_long` e `lyrics_mode`, e usa: (1) o cache de resultados de busca do servidor
(`youtube.recent_result(youtube_id)`, LRU com TTL de 1 h, alimentado por `GET /api/search`); (2) se não houver, uma
consulta de metadados (`youtube.probe(youtube_id)`, sem download; sem rede → `502`). Live → `400 invalid_request`.
**Defesa em profundidade:** o job do convidado leva `options.max_duration_ms`, e a etapa de download falha com o
código de job `video_too_long` se a duração real passar do limite.

**SSE**: no máximo **30** conexões `role=guest` no total e **3** por convidado. Acima disso:
`429 rate_limited {reason:"sse_limit"}`. Elas não contam em `stage.connected`.

**Filtro `visible_to`** (`events.py`), quando a assinatura é `role=guest`:
| Evento | Convidado recebe? |
|---|---|
| `hello` | sim, versão de convidado: `{server_time, client_id, guest, player, queue, jobs (só os dele, ativos)}`, sem `settings`/`system`/`guest_state` |
| `player.state`, `queue.updated`, `library.updated`, `song.updated` | sim |
| `job.updated`, `job.removed` | só quando `job.requested_by == principal.id` (`job.removed` é resolvido por um índice id→requested_by mantido pelo bus) |
| `guest.ended` | sim (só convidados) |
| `player.position`, `settings.updated`, `system.updated`, `toast`, `lyrics.*`, `guest.state` | **não** (`guest.state` traz a URL com o token) |

## Decisão 6 — Nome de quem pediu: coluna `added_by_name` (migração v4)

Resolver o nome pela sessão não serve, porque as sessões vivem só em memória e somem ao encerrar ou reiniciar, mas a
fila persiste. Por isso o nome fica gravado no item:
```sql
-- MIGRATIONS[3] → user_version 4
ALTER TABLE queue_items ADD COLUMN added_by_name TEXT;   -- nome do convidado no momento do pedido; NULL = host
```
- `QueueItem.added_by_name` e `PlayerState.up_next.added_by_name` passam a existir no contrato (AC-16.1, F9).
- `singer` padrão = nome do convidado, editável (≤ 40); `added_by_name` é sempre o nome da sessão.
- `jobs.requested_by` (que já existe) recebe `g_<n>` e aparece como `Job.requested_by`.
- **`Song.added_by` de música preparada por convidado = `"host"`.** A biblioteca é do dono da máquina, e o convidado
  não tem rota para excluir músicas. Sem isso, pela regra de posse (DECISION 0012), ninguém conseguiria excluí-las.
- **Exclusão de música com itens pendentes de outros atores** (pendência da v0.2, api.md §6):
  `DELETE /api/songs/{id}` → `409 song_queued_by_others {count}`. Isso fica coerente com a DECISION 0012: o host não
  remove, nem indiretamente, pedidos de convidados. *Ver o ponto em aberto abaixo.*

## Decisão 7 — Estado do modo no Palco e no Controle; página `/guest`

- Objeto **`GuestState`** (api.md §12.1): `enabled`, `url` (com o token), `display_url` (sem o token), `qr`
  (`/api/guest/qr?v=<version>`), `invite_version`, `invite_expires_at`, `sessions`, `connected`, `address`, `port`,
  `addresses[]`, `firewall_hint`, `error`.
- **Evento SSE `guest.state`** (só `control`/`stage`, que são host) a cada ligar, desligar, rotacionar, encerrar,
  sessão criada ou expirada e conexão SSE de convidado aberta ou fechada (no máximo 1 por segundo). O `hello` de
  `control`/`stage` inclui `guest_state`. O Palco troca o `<img src>` do QR quando `qr` muda; a meta de 2 s do
  AC-11.3 é folgada.
- **Quando o Palco mostra o QR** é regra de UI (DESIGN): `guest_state.enabled` e `player.status ∈ {idle, ended,
  up_next, waiting_gesture}`. Nunca em `playing`, `paused` ou `loading` (AC-12.2).
- **Página `/guest`**: `GET /guest` `[public]` serve `karaoke/web/guest.html`. Os assets ficam em
  `/static/css/guest.css` e `/static/js/guest/*.js` (o escopo `/static/*` passa a `public`). Reaproveita
  `js/shared/{api,events,format}.js` e `components/dom.js`, **sem editá-los**. Sem framework; meta < 150 KB.
  CSP inalterada.

---

## Ponto em aberto (precisa de DECISION do orchestrator/usuário)

**SPEC AC-16.1 × DECISION 0012.** A AC-16.1 diz que "o anfitrião continua podendo [...] remover qualquer item". A
DECISION 0012, confirmada pelo usuário, diz que "a regra de posse não tem exceção para o host", com o teste
"host não remove item de guest". Os contratos desta ADR **seguem a DECISION 0012**: o host reordena, pula
(`next`/`start`) e limpa a fila (`DELETE /api/queue`), mas `DELETE /api/queue/{id}` de um item de convidado devolve
`403 not_owner`. Se a decisão for dar ao host poder de moderação, a mudança é pequena: em `can_remove`, o host pode
remover itens com `added_by` começando por `g_`, e o `409 song_queued_by_others` vira cascata. Os contratos e os
testes mudam em 2 pontos.

---

## Plano de implementação sugerido (blocos sem arquivos em comum)

Dá para paralelizar **A ∥ B ∥ C**. Os três dependem só desta ADR e de `api.md`. O bloco A pode ser dividido em
A1 → A2 (ou A1 ∥ A2, se houver 4 vagas), também sem arquivos em comum.

**A1 — backend: infraestrutura do modo convidado** (backend-engineer)
- novos: `karaoke/guest/__init__.py`, `guest/service.py` (GuestService: convite, sessões, `GuestState`, enable,
  disable, rotate, end; `startup(app)`/`shutdown(app)`; timer de rotação), `guest/listener.py` (LanListener, decisão 1),
  `guest/netinfo.py` (decisão 2), `guest/qr.py` (decisão 3), `guest/ratelimit.py` (decisão 5), `karaoke/api/guest.py`
  (rotas de api.md §12)
- alterados: `karaoke/security.py` (escopo `public`, papel `anon`, ramo `guest`, hosts e origens dinâmicos),
  `karaoke/events.py` (`ROLES += guest`, `visible_to`, `close_role`, índice de jobs, contagem por principal),
  `karaoke/api/events.py` (`role=guest`, hello de convidado, limites de conexão, `guest_state` no hello do host),
  `karaoke/__main__.py` (`begin_shutdown` → `listener.request_stop()`), `karaoke/app.py` (**exceção autorizada** ao
  "engenheiros não editam": `ROUTER_MODULES += "guest"`, `LIFECYCLE_MODULES += "karaoke.guest.service"` (antes do
  runner), `app.state.rate_limiter`, rota `GET /guest` `[public]`, `/static/*` → `public`), `pyproject.toml`
  (`segno~=1.6.6`), `karaoke/errors.py` (helpers dos novos códigos)
- testes: `tests/api/test_guest_auth.py` (escopos, 401/403, cookie, Host/Origin dinâmicos, deny-by-default),
  `tests/api/test_guest_session.py`, `tests/unit/test_ratelimit.py`, `tests/unit/test_netinfo.py` (classificação com
  dados simulados), `tests/unit/test_guest_listener.py` (start/stop em `127.0.0.2` ou em porta efêmera; o stop
  termina com SSE aberto)

**A2 — backend: regras de convidado nas rotas existentes** (backend-engineer)
- novo: `karaoke/guest/policy.py` (cotas, limite de duração, saneamento do nome, `job_visible_to`)
- alterados: `karaoke/db.py` (migração v4), `karaoke/services/queue.py` (`added_by_name`), `karaoke/services/player.py`
  (`up_next.added_by_name`), `karaoke/api/queue.py` (POST de convidado: `end`, singer padrão, cota; DELETE passa a
  `guest-safe`, com posse e `409 queue_item_playing`), `karaoke/api/jobs.py` (POST de convidado: metadados do servidor,
  cotas, `requested_by`, `enqueue` forçado; GET `{id}` só os próprios), `karaoke/api/search.py` (alimenta o cache de
  resultados), `karaoke/api/songs.py` (`409 song_queued_by_others`), `karaoke/youtube.py` (`recent_result`, `probe`),
  `karaoke/jobs/runner.py` (`Job.requested_by`, `options.max_duration_ms` → `video_too_long`)
- testes: `tests/api/test_guest_queue.py`, `tests/api/test_guest_jobs.py`, `tests/unit/test_migration_v4.py`
- interface com A1: só `Principal` (que já existe) e `current_principal`; os testes de A2 injetam
  `Principal(role="guest", ...)`.

**B — frontend: Controle e Palco** (frontend-engineer)
- novos: `karaoke/web/js/control/guest-panel.js`, `karaoke/web/js/stage/guest-qr.js`
- alterados: `karaoke/web/index.html`, `karaoke/web/stage.html`, `karaoke/web/css/control.css`,
  `karaoke/web/css/stage.css`, `karaoke/web/js/control/main.js`, `js/control/queue.js` ("pedido por <nome>"),
  `js/stage/main.js`, `js/stage/screens.js` (QR na espera e no "A seguir", "Pedido por <nome>")
- **dono exclusivo de qualquer edição em `karaoke/web/js/shared/**`** e em `css/base.css`.

**C — frontend: página `/guest`** (frontend-engineer, 2ª instância)
- novos (só arquivos novos): `karaoke/web/guest.html`, `karaoke/web/css/guest.css`, `karaoke/web/js/guest/main.js`,
  `js/guest/session.js` (fragmento → `replaceState` → `sessionStorage` até criar a sessão), `js/guest/search.js`,
  `js/guest/my-queue.js`, `js/guest/request-sheet.js`
- **importa** `js/shared/*` sem editar. Se precisar mudar algo ali, abre um `CHANGE_REQUEST`, porque o dono é o B.

**Depois**: README (F8: uso, `ufw`/`firewall-cmd`, rede doméstica) → release-engineer. Roteiro de teste com celular
real → QA (usuário executa).

## Consequências
- Positivas: com o modo desligado, a superfície de rede é igual à da v0.2; docker e VPN nunca ficam expostos; o
  `authorize` continua o único ponto de decisão; a regra de posse e o deny-by-default seguem iguais; e há só uma
  dependência nova, pequena e sem dependências transitivas.
- Negativas e riscos: o 2º `uvicorn.Server` no mesmo loop depende de detalhes do uvicorn (`capture_signals`,
  `serve(sockets=)`), e isso fica coberto por teste de start/stop. A troca de IP derruba as sessões, porque o cookie
  é por host. HTTP sem TLS na LAN (aceito; aviso na UI). Sessões e convites se perdem ao reiniciar (desejado: AC-11.5).
- Pré-requisitos para o usuário: liberar `<porta>/tcp` no firewall do SO, se houver um ativo (README).
