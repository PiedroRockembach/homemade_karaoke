# ADR-0006 — Windows (WSL): rede para a LAN, firewall na instalação e diagnóstico do modo convidado

- Status: Aceito (2026-10-21)
- Data: 2026-10-21
- Autor: architect · Ciclo: `20261021-0239-karaoke` · Tarefa: T-001 · Requisitos: R1–R5 do BRIEF
- Complementa: [ADR-0003](0003-guest-mode.md) §1 (listener da LAN) e §2 (descoberta do IP, dica de firewall)
- Relacionados: [api.md §12.1](../contracts/api.md), README §"Modo convidado"

## Contexto

No Windows a app roda **dentro do WSL2** (`karaoke.bat` → `wsl -- make start`). Os celulares não abrem o QR porque:

1. **Rede NAT do WSL (padrão)**: o `eth0` do WSL tem um IP interno (`172.16/12`) que a LAN não alcança. O
   `netinfo.discover()` não sabe que está no WSL e põe esse IP no QR.
2. **Firewall do Windows**: bloqueia a entrada, e mais ainda com a rede marcada como **Pública**. No modo *mirrored* do
   WSL entra também o **firewall do Hyper-V**, que filtra o tráfego que chega à VM do WSL.
3. **Linux**: o `make install` não mexe em firewall. Hoje só existe a dica de comando no card (`firewall_hint`).

Fatos que restringem a decisão:
- O servidor principal só escuta em `127.0.0.1`. O listener da LAN abre **no IP escolhido** e só enquanto o modo
  convidado está ligado (ADR-0003 §1). **Nada passa a escutar em `0.0.0.0`**, nem no WSL nem no Windows.
- O papel `host` vale para "cliente loopback". Encaminhar a LAN para o `127.0.0.1` do WSL (o *localhost forwarding*)
  transformaria **todo convidado em anfitrião**, por isso essa opção fica descartada.
- A porta efetiva fica entre 8765 e 8774 (`DEFAULT_PORT` + `PORT_FALLBACK_TRIES`). As regras cobrem essa faixa inteira.
- Público pequeno e confiável (≤ 10 pessoas). Segurança não é a prioridade, mas o padrão continua conservador.

## Opções consideradas (rede do WSL)

1. **A. WSL *mirrored*** (`%UserProfile%\.wslconfig` → `[wsl2] networkingMode=mirrored`). O WSL passa a ver as
   interfaces do Windows com os mesmos IPs, e o listener abre direto no IP da Wi-Fi/Ethernet. Exige Windows 11 22H2+
   (build ≥ 22621) e WSL ≥ 2.0, mais uma regra no firewall do Hyper-V e um `wsl --shutdown` para valer.
2. **B. `netsh interface portproxy` v4tov4**: `<IP do Windows na LAN>:<porta>` → `<IP do WSL>:<porta>`. Funciona no
   Windows 10 e no 11. O IP do WSL muda a cada boot do WSL, então a regra tem que ser reaplicada no start (com admin).
3. **C. Documentar só os comandos no README** (o usuário faz tudo à mão).

### Matriz (1–5, peso entre parênteses)

| Critério                                        | A mirrored | B portproxy | C manual |
|-------------------------------------------------|:----------:|:-----------:|:--------:|
| Atende R1/R2 (×3)                               | 5          | 4           | 2        |
| Simplicidade / poucas peças (×3)                | 4          | 2           | 5        |
| Facilidade para o usuário (×2)                  | 4          | 3           | 1        |
| Cobertura de versões do Windows (×2)            | 2          | 5           | 5        |
| Estabilidade (sem reaplicar a cada boot) (×1)   | 5          | 2           | 3        |
| Impacto em outras distros/apps WSL (×1)         | 3          | 5           | 5        |
| **Total ponderado**                             | **47**     | **43**      | **40**   |

## Decisão 1 — A, com B como alternativa: detecção, confirmação e UAC

O `scripts/windows/setup-network.ps1` cuida da parte do Windows. O `karaoke.bat` o chama assim:

- `karaoke.bat --configurar-rede` → `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\setup-network.ps1`
- `karaoke.bat --desfazer-rede` → o mesmo script com `-Undo`
- **1ª instalação**: depois do `make install` dar certo, o bat pergunta
  `Configurar agora a rede para os celulares dos convidados? [S/N]` (`choice /c SN`). Com `N`, mostra o comando
  `karaoke.bat --configurar-rede` e segue normalmente. Essa pergunta não volta mais quando o arquivo de estado (abaixo)
  já existe ou depois de recusada (fica gravado `declined` no estado).

Fluxo do script (idempotente, mensagens em pt-BR, nada muda sem confirmação):
1. **Elevação**: sem admin, o script se relança com `Start-Process powershell -Verb RunAs -Wait` (UAC) repassando os
   parâmetros. Se o UAC for negado, sai com código 2 e uma mensagem. Nunca falha o `karaoke.bat`: o start continua.
2. **Detecção**: build pelo `[Environment]::OSVersion.Version.Build`, versão do WSL pelo `wsl.exe --version` (o
   código ≠ 0 indica WSL antigo ou da caixa) e modo atual pelo `.wslconfig` (`networkingMode`).
3. **Plano**: imprime o que vai fazer e pede `[S/N]` para cada passo que tem efeito colateral:
   - **Build ≥ 22621 e WSL ≥ 2.0 → modo A (recomendado)**. O script edita o `.wslconfig` **por mesclagem**: preserva
     as outras chaves, cria `[wsl2]` se faltar e define `networkingMode=mirrored`. Antes, faz backup em
     `%LOCALAPPDATA%\Karaoke\wslconfig.bak`. Depois oferece `wsl --shutdown`, avisando que todas as distros abertas
     serão fechadas. Se o usuário recusar a edição do `.wslconfig`, o script oferece o modo B.
   - **Senão → modo B**. Cria as regras de portproxy com `listenaddress` = **cada IPv4 do Windows que tem gateway
     padrão** (nunca `0.0.0.0`) e `connectaddress` = IP do `eth0` do WSL, obtido com
     `wsl.exe -e sh -c "ip -4 -o addr show eth0"`, para as portas 8765–8774. Confere se o serviço `iphlpsvc` está
     rodando e o inicia se preciso. Também registra a tarefa agendada `\Karaoke\AtualizarRede` ("executar com
     privilégios mais altos", disparo só sob demanda), que roda o script com `-Refresh`.
4. **Firewall** (Decisão 2) e **perfil de rede** (Decisão 3).
5. Grava o estado em `%LOCALAPPDATA%\Karaoke\rede.json`: `{mode: "mirrored"|"portproxy"|"declined", ports: [8765, 8774],
   profiles, created_at}`.

**Refresh no start (só modo B)**: antes do `make start`, o bat roda `schtasks /run /tn "\Karaoke\AtualizarRede"`. A
tarefa foi criada elevada, então roda **sem novo UAC**. Ela reaplica o portproxy com o IP atual do WSL e com os IPs
atuais do Windows. Se a tarefa não existir, o bat só avisa ("rode `karaoke.bat --configurar-rede`") e segue.

**Desfazer** (`-Undo`, também elevado e idempotente): remove as regras do grupo `Karaoke` no Windows Defender, a
regra do Hyper-V, o portproxy das portas 8765–8774 e a tarefa agendada. Se o `networkingMode` foi o script que
definiu (está registrado no `rede.json`), restaura o `.wslconfig` a partir do backup, com confirmação, e oferece
`wsl --shutdown`. Por fim, apaga o `rede.json`. O perfil de rede **não** é revertido: o script avisa como mudar de
volta nas Configurações.

## Decisão 2 — Regras de firewall

| Onde | Nome fixo | Comando (resumo) | Quando |
|------|-----------|------------------|--------|
| Windows Defender | `Karaoke-TCP-In` (Group `Karaoke`, DisplayName `Karaokê — convidados (TCP 8765-8774)`) | `New-NetFirewallRule -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765-8774 -Profile Private -RemoteAddress LocalSubnet` | sempre (A e B) |
| Firewall do Hyper-V | `Karaoke-WSL-TCP-In` | `New-NetFirewallHyperVRule -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Direction Inbound -Action Allow -Protocol TCP -LocalPorts 8765-8774` | modo A, se o cmdlet existir (`Get-Command`) |

- A idempotência é por nome: se a regra existe, o script remove e recria.
- **Perfis**: o padrão é só **Privado**. O script pergunta
  `Liberar também em redes Públicas? (só se você confia em todos dessa rede) [s/N]`. Com "sim", a regra fica com
  `-Profile Private,Public`.
- **Não** usamos `Set-NetFirewallHyperVVMSetting -DefaultInboundAction Allow`, porque abriria todas as portas do WSL. A
  regra fica restrita à faixa de portas.
- `-RemoteAddress LocalSubnet` limita a regra à sub-rede local (convidados na mesma Wi-Fi). Se a regra do Hyper-V não
  aceitar o parâmetro naquela build, ela fica sem a restrição.

## Decisão 3 — Perfil de rede Público

`Get-NetConnectionProfile`, para as interfaces que têm gateway padrão. Se alguma estiver como `Public`, o script
explica o efeito e pergunta `Marcar a rede "<Nome>" como Privada? [S/N]` →
`Set-NetConnectionProfile -InterfaceIndex <i> -NetworkCategory Private`. Redes `DomainAuthenticated` não são tocadas.

## Decisão 4 — Linux: passo opcional no `make install`

Novo `scripts/firewall-linux.sh [--undo]`, chamado no fim do `scripts/install.sh` e também exposto como
`make firewall` / `make firewall-undo`. Regras:
- **No WSL** (detecção da Decisão 5), o passo é pulado: o script avisa que no Windows a rede é configurada por
  `karaoke.bat --configurar-rede`.
- **Detecção sem root**: `ufw` ativo = `systemctl is-active --quiet ufw` **e** `ENABLED=yes` em `/etc/ufw/ufw.conf`.
  `firewalld` ativo = `systemctl is-active --quiet firewalld` (ou `firewall-cmd --state`). Se nenhum estiver ativo,
  o script imprime "nenhum firewall ativo detectado" e sai com 0.
- **Pergunta** `Liberar as portas 8765-8774/tcp no <ufw|firewalld> para os convidados? (pede sudo) [S/n]` só se
  `[ -t 0 ]`. Sem TTY, pula. `KARAOKE_FIREWALL=yes|no` força a resposta (para CI e scripts).
- **Comandos**: `sudo ufw allow 8765:8774/tcp comment 'karaoke convidados'` ·
  `sudo firewall-cmd --permanent --add-port=8765-8774/tcp && sudo firewall-cmd --reload`. O `--undo` faz o inverso
  (`ufw delete allow 8765:8774/tcp` · `--remove-port`).
- **Nunca falha a instalação**: o `install.sh` chama `bash scripts/firewall-linux.sh || echo "AVISO: …"`. Se o sudo
  for negado ou falhar, o script imprime o comando para rodar à mão e sai com 0.

## Decisão 5 — IP do QR dentro do WSL (backend)

Novo módulo `karaoke/guest/winnet.py`. Ele só usa a biblioteca padrão, e a leitura do sistema fica separada da parte
pura, que é testável com dados simulados.

1. **Plataforma** (`detect_platform()`, calculada uma vez): é WSL se `"microsoft"` aparece em `/proc/version`
   (minúsculas) **ou** se `WSL_INTEROP`/`WSL_DISTRO_NAME` existe. O modo vem de `wslinfo --networking-mode`
   (timeout de 2 s): `mirrored` → `wsl-mirrored`, `nat` → `wsl-nat`. Se o comando faltar ou falhar, a heurística é:
   algum IPv4 do Windows (item 2) presente numa interface local → `wsl-mirrored`; senão, `eth0` em `172.16/12` →
   `wsl-nat`; senão → `wsl-unknown`. Fora do WSL o valor é `linux`.
2. **Sonda do Windows** (`probe_windows()`, só em WSL): **uma** chamada a
   `powershell.exe -NoProfile -NonInteractive -Command <script embutido>`, timeout de 6 s, executada fora do event
   loop (`asyncio.to_thread`), com resultado em cache por 60 s. A primeira sonda dispara no startup, em background. A
   saída é JSON:
   `{ips:[{address, alias, kind:"wifi"|"ethernet", has_gateway, category:"Public"|"Private"|"DomainAuthenticated"}],
   portproxy:[{listen, port, connect}], fw_rule:bool, hyperv_rule:bool|null}`. Fontes: `Get-NetIPConfiguration`,
   `Get-NetAdapter` (`PhysicalMediaType` contendo `802.11` → `wifi`), `Get-NetConnectionProfile`,
   `netsh interface portproxy show v4tov4` e `Get-NetFirewallRule -Name Karaoke-TCP-In`. Nada disso exige admin. Se
   a sonda falhar (interop desligado, timeout, JSON inválido), devolve `None` e o motivo fica no log. **Não** fazemos
   parse do `ipconfig.exe`, porque a saída dele muda conforme o idioma do Windows.
3. **Candidatos** (`addresses()`):
   - `linux` e `wsl-mirrored`: o `netinfo.discover()` continua igual. No mirrored, o `kind` de cada IP é corrigido
     pelo da sonda quando o IP coincide (o WSL não expõe `/sys/.../wireless`), e as interfaces sem gateway no Windows
     (`vEthernet` de Hyper-V/Docker) são descartadas quando a sonda existe.
   - `wsl-nat`: os candidatos são os **IPs do Windows com gateway** vindos da sonda, cada um com
     `bind_address` = IPv4 do `eth0` do WSL. Sem sonda, a lista volta a ser o `discover()` atual (o IP 172.x), e o
     diagnóstico `windows_ip_unknown` acende.
   - Override manual: `KARAOKE_GUEST_ADVERTISE=<ip>` (variável de ambiente) põe esse IP como único candidato
     recomendado, com `bind_address` = `eth0` quando o modo é `wsl-nat`. É a saída de emergência documentada no README.
4. **Endereço anunciado × endereço de bind** (correção necessária no `GuestService`): `self.address` continua sendo o
   que vai na URL, no QR e no `allowed_hosts()`. Um novo `self.bind_address` (que é igual a `address` fora do
   `wsl-nat`) é o IP passado ao `listener.start()` e comparado em `accepts_connection()`
   (`request.scope["server"][0] == bind_address`). O Host header que o celular envia é o IP do Windows, e ele continua
   sendo validado contra `address:port`.
5. **Risco aceito (modo B)**: com o portproxy, todos os convidados chegam com o IP do Windows na rede do WSL
   (`172.x.x.1`). Os limites por IP (`POST /api/guest/session` etc.) passam a ser compartilhados. A folga para cerca
   de 10 pessoas atrás de um NAT (DECISION 0016) cobre isso. O backend não muda os limites.

## Decisão 6 — Diagnóstico (R4)

**Card do Controle**: o `GuestState` ganha `platform` e `diagnostics[]` (veja api.md §12.1). O backend os calcula a
partir do cache (sem custo no `state()`). Códigos:

| code | Quando acende (detectável) | Mensagem (resumo) · `command` |
|------|----------------------------|-------------------------------|
| `wsl_nat_unconfigured` | `wsl-nat` e nenhum portproxy na porta efetiva | "O Windows ainda não encaminha a porta para o WSL." · `karaoke.bat --configurar-rede` |
| `wsl_portproxy_stale` | `wsl-nat`, existe portproxy na porta, mas `connect` ≠ IP atual do `eth0` | "O encaminhamento aponta para um IP antigo do WSL. Feche e abra pelo karaoke.bat." · `karaoke.bat` |
| `windows_firewall_rule_missing` | WSL e `fw_rule == false` | "A porta não está liberada no Firewall do Windows." · `karaoke.bat --configurar-rede` |
| `windows_hyperv_rule_missing` | `wsl-mirrored` e `hyperv_rule == false` | "Falta liberar a porta no firewall do Hyper-V (WSL)." · `karaoke.bat --configurar-rede` |
| `windows_network_public` | IP em uso (ou recomendado) com `category == "Public"` | "A rede \"<alias>\" está como Pública no Windows; marque-a como Privada." · `karaoke.bat --configurar-rede` |
| `windows_ip_unknown` | WSL e a sonda falhou | "Não consegui descobrir o IP do Windows. Veja o README (Windows) ou defina KARAOKE_GUEST_ADVERTISE." · `null` |
| `vpn_present` | há candidato `kind == "vpn"` | "Uma VPN está ativa; se os celulares não entrarem, desligue-a." · `null` |

- `firewall_hint` muda de forma: no WSL vira `{tool:"windows", command:"karaoke.bat --configurar-rede"}`. Em Linux
  continua igual (ufw/firewall-cmd por porta).
- O que não dá para detectar fica **só no README**: isolamento de clientes ou rede de convidados do roteador, celular
  nos dados móveis, antivírus com firewall próprio (Kaspersky, ESET, Norton, Avast etc. substituem o do Windows),
  economia de energia da Wi-Fi no Linux (`iw dev <if> set power_save off`) e VPN no celular.

**README** (o release-engineer escreve):
- nova seção "Modo convidado no Windows (WSL)", com o que o `--configurar-rede` faz (mirrored × portproxy,
  firewall, perfil), como desfazer (`--desfazer-rede`) e os comandos manuais equivalentes em PowerShell (admin);
- §"Liberar a porta no firewall" (Linux), atualizada com `make firewall` / `make firewall-undo` e a faixa 8765–8774;
- tabela de problemas acrescida das linhas: rede Pública, antivírus de terceiros, WSL NAT (IP 172.x no QR), portproxy
  desatualizado e Wi-Fi power save.

## Consequências

- (+) No Windows 11, um único `--configurar-rede` resolve de forma estável. No Windows 10, o portproxy funciona sem novo
  UAC a cada start.
- (+) Nada passa a escutar em `0.0.0.0`. Sem o modo convidado ligado, a superfície é a mesma da v0.3 (R5): as regras de
  firewall só abrem a porta, e não há listener até o host ligar o modo.
- (−) O modo mirrored muda a rede de **todas** as distros WSL do usuário. Por isso há confirmação, backup e um
  desfazer.
- (−) O `powershell.exe` via interop custa entre 1 e 3 s, o que é mitigado com o cache de 60 s, a execução em thread e
  a sonda feita no startup.
- (−) Sem testes automatizados possíveis para o `.ps1` e o `.bat` neste ambiente (Linux). A validação é manual, feita
  pelo usuário no Windows. A parte pura do `winnet` (parse da sonda, plataforma, diagnósticos) tem testes com
  dados simulados.

## Plano de implementação (tarefas paralelas, sem arquivo em comum)

| Tarefa | Dono | Arquivos |
|--------|------|----------|
| Windows + Linux instalação/README | release-engineer | `karaoke.bat`, `scripts/windows/setup-network.ps1` (novo), `scripts/firewall-linux.sh` (novo), `scripts/install.sh`, `Makefile`, `README.md` |
| WSL no backend | backend-engineer | `karaoke/guest/winnet.py` (novo), `karaoke/guest/netinfo.py`, `karaoke/guest/service.py`, `tests/test_winnet.py` (novo), testes de `service`/`netinfo` existentes |
| Card do Controle | frontend-engineer | `karaoke/web/js/control/guest-panel.js` (+ CSS do painel, se precisar): render de `diagnostics[]` e de `firewall_hint.tool == "windows"` |

A interface entre as três tarefas é só o contrato (api.md §12.1) e os nomes fixos desta ADR: `--configurar-rede`,
`--desfazer-rede`, `Karaoke-TCP-In`, `Karaoke-WSL-TCP-In`, `\Karaoke\AtualizarRede`, `KARAOKE_GUEST_ADVERTISE` e
`KARAOKE_FIREWALL`.
