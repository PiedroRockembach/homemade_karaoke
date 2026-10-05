# ADR-0002 — Preparação para o modo convidado (LAN + QR)

- Status: substituído parcialmente por [ADR-0003](0003-guest-mode.md) (2026-10-05) — §1 (LAN via `KARAOKE_HOST`) e os itens "futuro" foram decididos lá; papéis, `authorize` e deny-by-default continuam valendo
- Data: 2026-10-04
- Autor: architect · Ciclo: `20261003-2339-karaoke` · Tarefa: T-003 · História: F14 (futuro)
- Relacionados: [ADR-0001](0001-stack.md), [api.md](../contracts/api.md), [ARCHITECTURE.md §9–10](../ARCHITECTURE.md)

## Contexto
O usuário quer, numa versão futura, mostrar um QR code para que pessoas na mesma rede abram uma página no celular,
busquem músicas e as adicionem à fila (SPEC §3.1). **Nada disso é implementado agora.** Esta ADR define apenas os
ganchos que a v1 precisa ter para que o modo convidado seja uma adição, não uma reescrita:
o servidor hoje escuta só em `127.0.0.1` e todos os clientes são o anfitrião.

## Opções consideradas
1. **Não preparar nada** — adicionar autorização quando o modo convidado vier. Barato agora, mas exige revisar
   rota por rota depois, com risco de esquecer alguma (o que expõe controle total na LAN).
2. **Fachada separada `/api/guest/*`** desde já — duplica rotas e payloads; é escopo extra na v1.
3. **Papéis + ponto único de autorização desde já, rotas compartilhadas** — cada rota declara o escopo
   (`host` | `guest-safe`); uma única dependência resolve o *principal* e decide. Hoje só existe `host`.

| Critério (peso)                          | 1 | 2 | 3 |
|------------------------------------------|---|---|---|
| Não aumenta o escopo da v1 (×3)          | 5 | 2 | 4 |
| Segurança quando abrir a LAN (×3)        | 2 | 5 | 4 |
| Simplicidade / poucas peças (×2)         | 5 | 2 | 4 |
| Custo de implementar o F14 depois (×2)   | 1 | 4 | 4 |
| **Total ponderado**                      | 35| 34| 40|

## Decisão
Opção 3. Concretamente:

### 1. Host e porta configuráveis, LAN como opt-in explícito
- `KARAOKE_HOST` (padrão `127.0.0.1`) e `KARAOKE_PORT` (padrão `8765`, fallback +10) já existem (ARCHITECTURE §8).
- **Na v1**, `config.py` aceita somente endereços de loopback (`127.0.0.0/8`, `::1`, `localhost`). Qualquer outro
  valor encerra a inicialização com a mensagem "Acesso pela rede ainda não está disponível nesta versão".
  Isso evita expor a API inteira por engano antes de existir autenticação de convidado.
- **No futuro**, abrir a LAN exigirá duas coisas juntas: `KARAOKE_HOST` não-loopback **e** `KARAOKE_GUEST_MODE=1`
  (ou o toggle "Modo convidado" no Controle, não persistido: volta a desligado a cada reinício).

### 2. Modelo de papéis
| Papel | Quem | Como é identificado |
|-------|------|---------------------|
| `host`  | Quem usa o próprio computador (Controle e Palco) | Conexão vinda de loopback **e** `Host` header permitido. Nunca por token. |
| `guest` | Celulares na LAN (futuro) | Cookie de sessão de convidado válido; conexão não-loopback. |

O *principal* resolvido por requisição é `{ role: "host" | "guest", id: "host" | "g_<n>", name: string | null }`.
Hoje toda requisição aceita resolve para `{role:"host", id:"host"}`. O `id` do principal é o que vai em
`QueueItem.added_by` (e, no futuro, em `jobs.requested_by`).

Cada rota é classificada (tabela em [api.md §0.1](../contracts/api.md)):
- **`host`** — controle total (player, reordenar/limpar fila, excluir, letras, settings, sistema, Palco).
- **`guest-safe`** — leitura de biblioteca/fila/estado, busca, enfileirar ao fim, pedir processamento — com as
  restrições de convidado anotadas na rota (ex.: `position` só `"end"`, sem `confirm_long`).
- **Deny-by-default:** rota sem classificação é tratada como `host`. Um teste da v1 percorre `app.routes` e falha se
  alguma rota `/api/*` ou `/media/*` não declarar escopo.

### 3. Ponto único de autorização
Módulo `karaoke/security.py`, aplicado como dependência global do app (não por rota esquecível):
```
authorize(request) -> Principal
  1. Host header ∈ hosts permitidos                       senão 403 forbidden   (anti DNS-rebinding)
  2. Mutação (POST/PUT/PATCH/DELETE):
       Origin ausente ou ∈ origens permitidas             senão 403 forbidden   (anti CSRF)
       Content-Type application/json quando há corpo      senão 400 invalid_request
  3. Cliente loopback → Principal(host)
     [futuro] cookie de convidado válido e modo ativo → Principal(guest)
     senão 403 forbidden  ([futuro] 401 guest_session_required)
  4. escopo da rota == host e principal.role == guest     → 403 forbidden
  5. [futuro] rate_limiter.check(principal, rota)         → 429 rate_limited
```
Na v1 os passos 1–3 rodam de verdade (só o ramo host) e o passo 4 é trivialmente satisfeito; o passo 5 é uma
interface `RateLimiter` com implementação no-op. Os passos 1–2 também fecham uma brecha real do modo localhost:
qualquer site aberto no navegador poderia, sem eles, enviar `POST` para `127.0.0.1:8765`.

### 4. Autenticação do convidado (futuro) — onde entra o token
- O anfitrião liga o modo convidado; o servidor gera um **token de convite** aleatório (≥ 128 bits, `secrets`),
  guardado só em memória (hash), com validade (ex.: até desligar o modo ou 12 h) e rotacionável ("Novo QR").
- **QR = URL com token no fragmento**: `http://<ip-lan>:<porta>/guest#t=<token>`. O fragmento não vai para logs de
  acesso, nem para `Referer`.
- A página `/guest` lê o fragmento, apaga-o da barra (`history.replaceState`) e troca por sessão:
  `POST /api/guest/session { token, name }` → cookie `karaoke_guest` (`HttpOnly; SameSite=Strict; Path=/`), id `g_<n>`.
- Rotacionar o convite não derruba sessões já criadas; "Encerrar convidados" invalida todas.
- O QR é gerado no servidor como SVG (`GET /api/guest/qr`, rota `host`) — sem CDN, sem request externo.

### 5. Implicações em CSP, SSE e CORS
- **CSP:** inalterada (`default-src 'self'`). A página `/guest` usa os mesmos assets same-origin; o QR é SVG servido
  pelo próprio app (`img-src 'self'` já cobre). Sem `unsafe-eval`, sem CDNs.
- **CORS:** nenhum header CORS, nem hoje nem no futuro — UI, API e mídia têm a mesma origem. Requisição cross-origin
  de mutação é recusada pelo passo 2.
- **SSE:** `/api/events` ganhará `role=guest` (hoje só `control|stage`; outro valor → `400`). Para convidados:
  evento `hello` sem `settings`/`system`; só `player.state`, `queue.updated`, `library.updated` e `job.updated` dos
  jobs que ele pediu; limite de conexões de convidado (ex.: 30) separado de `stage.connected`. O `EventBus` já
  publica por conexão — o filtro entra como função `visible_to(principal, event)` com implementação "tudo" na v1.
- **Mídia:** `instrumental.opus`/`vocals.opus` são `host` (convidados não precisam baixar áudio); thumbnails são
  `guest-safe`.

## Riscos ao abrir na LAN e mitigação futura
| Risco | Mitigação prevista |
|-------|-------------------|
| Expor a API inteira (controle do player, exclusão) | Deny-by-default + teste que exige escopo em toda rota; host só via loopback. |
| HTTP sem TLS na LAN (token/cookie farejáveis em rede pública) | Recomendar só redes domésticas confiáveis; aviso na UI ao ligar; sessão curta; convite rotacionável. TLS local fica fora (certificados autoassinados assustam celulares). |
| Token vazado (foto do QR compartilhada) | Validade curta, "Novo QR", "Encerrar convidados", modo desligado a cada reinício. |
| Abuso de recursos: muitos downloads/jobs de GPU, disco cheio | `RateLimiter` (ex.: busca 10/min, ≤ 3 jobs ativos e ≤ 2 itens pendentes por convidado), convidado não pode `confirm_long`, aprovação opcional pelo anfitrião. |
| DoS por conexões SSE | Limite de conexões de convidado; keep-alive já existente derruba conexões mortas. |
| DNS rebinding / CSRF | Passos 1–2 do `authorize` (já na v1); lista de hosts passa a incluir o IP/nome LAN configurado. |
| Nome do convidado com conteúdo malicioso | Limite de 40 chars, texto puro; UI usa `textContent` (regra já válida para `singer`). |
| Firewall do SO bloqueando a porta | Documentar no README do F14 (`firewall-cmd`/`ufw`). |

## Consequências
- Positivas: o F14 vira "implementar o ramo guest do `authorize`, a página `/guest` e 3 rotas"; a v1 ganha proteção
  anti-CSRF/DNS-rebinding de graça; `added_by` já existe nos dados.
- Negativas: um módulo (`security.py`) e uma coluna (`queue_items.added_by`) a mais na v1; disciplina de declarar
  escopo em cada rota (garantida por teste).
- Fora da v1 (não implementar): rotas `/api/guest/*`, página `/guest`, QR, cookies, rate limit real, filtro de SSE,
  bind não-loopback.
