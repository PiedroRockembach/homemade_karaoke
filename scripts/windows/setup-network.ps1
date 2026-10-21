<#
  Karaoke - prepara a rede do Windows para os celulares dos convidados (app rodando no WSL).
  Decisao: docs/adr/0006-windows-wsl-lan-and-firewall.md

  Uso (normalmente pelo karaoke.bat):
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\setup-network.ps1          configura
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\setup-network.ps1 -Undo    desfaz
    ... -Refresh   reaplica o portproxy com o IP atual do WSL (tarefa agendada \Karaoke\AtualizarRede)

  O que faz (sempre perguntando antes de cada passo):
    1. Windows 11 22H2+ com WSL >= 2.0: WSL em modo "mirrored" (.wslconfig, com backup).
       Senao: encaminhamento de portas (netsh portproxy) IP-do-Windows -> IP-do-WSL, portas 8765-8774.
    2. Firewall do Windows (+ firewall do Hyper-V no mirrored): entrada TCP 8765-8774, perfil Privado.
    3. Oferece marcar como Privada a rede que estiver como Publica.
  Idempotente. Nada passa a escutar em 0.0.0.0. Codigos de saida: 0 ok, 1 erro, 2 UAC negado.
  Arquivo so com ASCII de proposito: o Windows PowerShell 5.1 le .ps1 sem BOM como ANSI.
#>
[CmdletBinding()]
param(
    [switch]$Refresh,
    [switch]$Undo,
    [switch]$Elevated,
    [string]$UserHome = $env:USERPROFILE,
    [string]$UserLocalAppData = $env:LOCALAPPDATA
)

$ErrorActionPreference = 'Stop'

$PortFirst = 8765
$PortLast = 8774
$PortRange = "$PortFirst-$PortLast"
$FwRuleName = 'Karaoke-TCP-In'
$FwGroup = 'Karaoke'
$FwDisplay = "Karaok$([char]0x00EA) $([char]0x2014) convidados (TCP $PortRange)"
$HvRuleName = 'Karaoke-WSL-TCP-In'
$HvDisplay = "Karaok$([char]0x00EA) $([char]0x2014) convidados no WSL (TCP $PortRange)"
$WslVmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$TaskPath = '\Karaoke\'
$TaskName = 'AtualizarRede'
$StateDir = Join-Path $UserLocalAppData 'Karaoke'
$StateFile = Join-Path $StateDir 'rede.json'
$BackupFile = Join-Path $StateDir 'wslconfig.bak'
$LogFile = Join-Path $StateDir 'rede.log'
$WslConfig = Join-Path $UserHome '.wslconfig'
$IpHelperMarker = Join-Path $StateDir 'iphlpsvc-estava-desativado'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# ---------------------------------------------------------------- utilitarios

function Say([string]$msg) { Write-Host $msg }
function Ok([string]$msg) { Write-Host "  OK: $msg" -ForegroundColor Green }
function Warn([string]$msg) { Write-Host "AVISO: $msg" -ForegroundColor Yellow }

function Write-Log([string]$msg) {
    try {
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        $line = '{0} {1}' -f (Get-Date -Format 's'), $msg
        [System.IO.File]::AppendAllText($LogFile, $line + "`r`n", $Utf8NoBom)
    } catch { }
}

# Pergunta S/N. $Default: 'S', 'N' ou '' (resposta obrigatoria, sem padrao).
function Ask([string]$Question, [string]$Default) {
    $hint = switch ($Default) { 'S' { '[S/n]' } 'N' { '[s/N]' } default { '[S/N]' } }
    while ($true) {
        $a = Read-Host "$Question $hint"
        if ($null -eq $a) { $a = '' }
        $a = $a.Trim().ToLower()
        if ($a -eq '' -and $Default -eq 'S') { return $true }
        if ($a -eq '' -and $Default -eq 'N') { return $false }
        if ($a -match '^(s|sim|y|yes)$') { return $true }
        if ($a -match '^(n|nao|no)$') { return $false }
        Say '  Responda S (sim) ou N (nao).'
    }
}

# Roda um programa nativo sem que o stderr vire excecao (PowerShell 5.1 + Stop).
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @Arguments 2>$null
        return [pscustomobject]@{ Code = $LASTEXITCODE; Out = @($out) }
    } catch {
        return [pscustomobject]@{ Code = -1; Out = @() }
    } finally {
        $ErrorActionPreference = $eap
    }
}

function Test-Admin {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------- deteccao

function Get-WslVersion {
    $old = $env:WSL_UTF8
    $env:WSL_UTF8 = '1'   # wsl.exe fala UTF-16 por padrao
    try { $r = Invoke-Native 'wsl.exe' @('--version') } finally { $env:WSL_UTF8 = $old }
    if ($r.Code -ne 0 -or $r.Out.Count -eq 0) { return $null }   # WSL antigo (da caixa) nao tem --version
    $text = ($r.Out -join "`n") -replace "`0", ''
    $m = [regex]::Match($text, '(\d+)\.(\d+)\.(\d+)')
    if (-not $m.Success) { return $null }
    return [version]('{0}.{1}.{2}' -f $m.Groups[1].Value, $m.Groups[2].Value, $m.Groups[3].Value)
}

function Get-WslConfigMode {
    if (-not (Test-Path -LiteralPath $WslConfig)) { return $null }
    $section = ''
    foreach ($line in [System.IO.File]::ReadAllLines($WslConfig)) {
        $t = $line.Trim()
        if ($t -match '^\[(.+)\]$') { $section = $Matches[1].Trim().ToLower(); continue }
        if ($section -eq 'wsl2' -and $t -match '^networkingMode\s*=\s*"?([A-Za-z]+)') { return $Matches[1].ToLower() }
    }
    return $null
}

# Mescla networkingMode=mirrored em [wsl2], preservando o resto do arquivo.
function Set-WslConfigMirrored {
    $lines = New-Object System.Collections.Generic.List[string]
    if (Test-Path -LiteralPath $WslConfig) {
        foreach ($l in [System.IO.File]::ReadAllLines($WslConfig)) { $lines.Add($l) }
    }
    $inWsl2 = $false; $hasWsl2 = $false; $done = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $t = $lines[$i].Trim()
        if ($t -match '^\[(.+)\]$') {
            if ($inWsl2) { $lines.Insert($i, 'networkingMode=mirrored'); $done = $true; break }
            $inWsl2 = ($Matches[1].Trim() -eq 'wsl2')
            if ($inWsl2) { $hasWsl2 = $true }
            continue
        }
        if ($inWsl2 -and $t -match '^networkingMode\s*=') { $lines[$i] = 'networkingMode=mirrored'; $done = $true; break }
    }
    if (-not $done) {
        if (-not $hasWsl2) {
            if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne '') { $lines.Add('') }
            $lines.Add('[wsl2]')
        }
        $lines.Add('networkingMode=mirrored')   # [wsl2] e a ultima secao (ou acabou de ser criada)
    }
    [System.IO.File]::WriteAllLines($WslConfig, $lines, $Utf8NoBom)
}

# Interfaces do Windows com gateway padrao (Wi-Fi/Ethernet de verdade; vEthernet nao tem gateway).
function Get-LanConfigs {
    @(Get-NetIPConfiguration -ErrorAction SilentlyContinue | Where-Object {
            $_.IPv4DefaultGateway -and $_.IPv4Address -and $_.NetAdapter -and $_.NetAdapter.Status -eq 'Up'
        })
}

function Get-LanIps($configs) {
    @($configs | ForEach-Object { $_.IPv4Address } | ForEach-Object { $_.IPAddress } |
        Where-Object { $_ -and $_ -notlike '169.254.*' } | Select-Object -Unique)
}

function Get-WslIp {
    $r = Invoke-Native 'wsl.exe' @('-e', 'sh', '-c', 'ip -4 -o addr show eth0')
    if ($r.Code -ne 0) { return $null }
    $m = [regex]::Match(($r.Out -join ' '), 'inet (\d+\.\d+\.\d+\.\d+)')
    if ($m.Success) { return $m.Groups[1].Value }
    return $null
}

# ---------------------------------------------------------------- estado

function Read-State {
    if (Test-Path -LiteralPath $StateFile) {
        try { return (Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json) } catch { }
    }
    return $null
}

function Write-State([string]$Mode, [string[]]$Profiles, [bool]$WslChanged, [bool]$WslExisted) {
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $o = [ordered]@{
        mode              = $Mode
        ports             = @($PortFirst, $PortLast)
        profiles          = @($Profiles)
        created_at        = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        wslconfig_changed = $WslChanged
        wslconfig_existed = $WslExisted
    }
    [System.IO.File]::WriteAllText($StateFile, ($o | ConvertTo-Json), $Utf8NoBom)
}

# ---------------------------------------------------------------- portproxy (modo B)

function Get-KaraokePortProxy {
    $r = Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'show', 'v4tov4')
    foreach ($line in $r.Out) {
        if ($line -match '^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$') {
            $port = [int]$Matches[2]
            if ($port -ge $PortFirst -and $port -le $PortLast) {
                [pscustomobject]@{ Listen = $Matches[1]; Port = $port; Connect = $Matches[3] }
            }
        }
    }
}

function Remove-KaraokePortProxy {
    foreach ($e in @(Get-KaraokePortProxy)) {
        Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'delete', 'v4tov4',
            "listenaddress=$($e.Listen)", "listenport=$($e.Port)") | Out-Null
    }
}

function Set-KaraokePortProxy([string[]]$ListenIps, [string]$WslIp) {
    Remove-KaraokePortProxy
    $fail = 0
    foreach ($ip in $ListenIps) {
        for ($p = $PortFirst; $p -le $PortLast; $p++) {
            $r = Invoke-Native 'netsh.exe' @('interface', 'portproxy', 'add', 'v4tov4',
                "listenaddress=$ip", "listenport=$p", "connectaddress=$WslIp", "connectport=$p")
            if ($r.Code -ne 0) { $fail++ }
        }
    }
    return ($fail -eq 0)
}

function Enable-IpHelper {
    $svc = Get-Service -Name 'iphlpsvc' -ErrorAction SilentlyContinue
    if (-not $svc) { Warn 'Servico "Auxiliar IP" (iphlpsvc) nao encontrado; o portproxy depende dele.'; return }
    if ($svc.StartType -eq 'Disabled') {
        Set-Service -Name 'iphlpsvc' -StartupType Automatic
        # Marca para o -Undo devolver o servico ao estado original.
        New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
        [System.IO.File]::WriteAllText($IpHelperMarker, 'Disabled', $Utf8NoBom)
    }
    if ($svc.Status -ne 'Running') { Start-Service -Name 'iphlpsvc' }
}

function Register-RefreshTask {
    $arg = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Refresh -UserLocalAppData `"$UserLocalAppData`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Action $action -Principal $principal `
        -Settings $settings -Description 'Karaoke: reaplica o encaminhamento de portas para o IP atual do WSL.' -Force | Out-Null
}

function Unregister-RefreshTask {
    if (Get-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName -Confirm:$false
    }
}

# ---------------------------------------------------------------- firewall

function Remove-FirewallRules {
    Remove-NetFirewallRule -Name $FwRuleName -ErrorAction SilentlyContinue
    Get-NetFirewallRule -Group $FwGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    if (Get-Command Remove-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        Remove-NetFirewallHyperVRule -Name $HvRuleName -ErrorAction SilentlyContinue
    }
}

function Remove-HyperVRule {
    if (Get-Command Remove-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        Remove-NetFirewallHyperVRule -Name $HvRuleName -ErrorAction SilentlyContinue
    }
}

function Restore-IpHelper {
    if (-not (Test-Path -LiteralPath $IpHelperMarker)) { return }
    Stop-Service -Name 'iphlpsvc' -Force -ErrorAction SilentlyContinue
    Set-Service -Name 'iphlpsvc' -StartupType Disabled -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $IpHelperMarker -Force -ErrorAction SilentlyContinue
    Ok 'Servico "Auxiliar IP" (iphlpsvc) voltou a ficar desativado, como estava antes.'
}

function Set-FirewallRules([string[]]$Profiles, [bool]$HyperV) {
    Remove-NetFirewallRule -Name $FwRuleName -ErrorAction SilentlyContinue
    New-NetFirewallRule -Name $FwRuleName -DisplayName $FwDisplay -Group $FwGroup -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $PortRange -Profile ($Profiles -join ',') -RemoteAddress LocalSubnet | Out-Null
    Ok "Firewall do Windows: regra $FwRuleName (perfil $($Profiles -join ' e '))."
    Remove-HyperVRule
    if (-not $HyperV) { return }
    if (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue) {
        $hv = @{
            Name = $HvRuleName; DisplayName = $HvDisplay; VMCreatorId = $WslVmCreatorId; Direction = 'Inbound'
            Action = 'Allow'; Protocol = 'TCP'; LocalPorts = $PortRange; Profiles = ($Profiles -join ',')
        }
        $scope = 'sub-rede local'
        try {
            New-NetFirewallHyperVRule @hv -RemoteAddresses 'LocalSubnet' -ErrorAction Stop | Out-Null
        } catch {
            # Algumas builds nao aceitam LocalSubnet na regra do Hyper-V: recria sem restringir a origem.
            Write-Log "hyperv: -RemoteAddresses LocalSubnet recusado ($($_.Exception.Message)); regra sem restricao de origem"
            Remove-HyperVRule
            try {
                New-NetFirewallHyperVRule @hv -ErrorAction Stop | Out-Null
                $scope = 'qualquer origem'
            } catch {
                Write-Log "hyperv: falhou ($($_.Exception.Message))"
                Warn "Nao consegui criar a regra $HvRuleName no firewall do Hyper-V: $($_.Exception.Message)"
                return
            }
        }
        Ok "Firewall do Hyper-V (WSL): regra $HvRuleName (perfil $($Profiles -join ' e '), $scope)."
    } else {
        Warn 'Este Windows nao tem o firewall do Hyper-V (New-NetFirewallHyperVRule); passo pulado.'
    }
}

# ---------------------------------------------------------------- fluxos

function Invoke-Refresh {
    $state = Read-State
    if (-not $state -or $state.mode -ne 'portproxy') { Write-Log 'refresh: modo nao e portproxy; nada a fazer'; return 0 }
    $ips = Get-LanIps (Get-LanConfigs)
    $wslIp = Get-WslIp
    if (-not $wslIp -or $ips.Count -eq 0) {
        Write-Log "refresh: falhou (wsl=$wslIp, windows=$($ips -join ','))"
        return 1
    }
    Enable-IpHelper
    $ok = Set-KaraokePortProxy $ips $wslIp
    Write-Log "refresh: $($ips -join ',') -> $wslIp (ok=$ok)"
    if ($ok) { return 0 } else { return 1 }
}

function Invoke-Configure {
    Say ''
    Say '=== Karaoke: rede para os celulares dos convidados ==='
    $build = 0
    try { $build = [int](Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuildNumber } catch { }
    if (-not $build) { $build = [Environment]::OSVersion.Version.Build }
    if ($Elevated -and $UserHome -ne $env:USERPROFILE) {
        Warn "O UAC usou outra conta ($env:USERNAME). O modo mirrored funciona, mas o encaminhamento de portas (Windows 10)"
        Warn 'precisa que a sua propria conta seja administradora: o WSL e a tarefa agendada ficariam na conta do admin.'
    }
    $wslVer = Get-WslVersion
    $cfgMode = Get-WslConfigMode
    $prev = Read-State
    $lan = Get-LanConfigs
    $ips = Get-LanIps $lan
    $canMirrored = ($build -ge 22621) -and $wslVer -and ($wslVer -ge [version]'2.0.0')

    $wslTxt = if ($wslVer) { "$wslVer" } else { 'antigo (sem "wsl --version")' }
    Say "Windows build $build | WSL $wslTxt | .wslconfig networkingMode: $(if ($cfgMode) { $cfgMode } else { 'padrao (nat)' })"
    Say "IPs do Windows na rede: $(if ($ips.Count) { $ips -join ', ' } else { 'nenhum (conecte o PC ao Wi-Fi ou ao cabo)' })"
    Say ''

    $mode = $null
    $wslChanged = [bool]($prev -and $prev.wslconfig_changed)
    $wslExisted = if ($prev -and $null -ne $prev.wslconfig_existed) { [bool]$prev.wslconfig_existed } else { Test-Path -LiteralPath $WslConfig }

    if ($cfgMode -eq 'mirrored') {
        $mode = 'mirrored'
        Ok 'O WSL ja esta no modo mirrored (mesmos IPs do Windows).'
    } elseif ($canMirrored) {
        Say 'Recomendado: modo "mirrored" do WSL. O WSL passa a usar os mesmos IPs do Windows e os celulares'
        Say 'alcancam a app direto. Isso muda a rede de TODAS as distros WSL deste usuario.'
        Say "Sera editado $WslConfig (so a chave networkingMode em [wsl2]; backup em $BackupFile)."
        if (Ask 'Alterar o .wslconfig para networkingMode=mirrored?' '') {
            New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
            $wslExisted = Test-Path -LiteralPath $WslConfig
            if ($wslExisted) { Copy-Item -LiteralPath $WslConfig -Destination $BackupFile -Force }
            Set-WslConfigMirrored
            $wslChanged = $true
            $mode = 'mirrored'
            Ok '.wslconfig atualizado.'
            Say 'Para valer, o WSL precisa reiniciar: TODAS as distros e programas abertos no WSL serao fechados'
            Say '(inclusive um karaoke que esteja rodando).'
            if (Ask 'Reiniciar o WSL agora (wsl --shutdown)?' '') {
                Invoke-Native 'wsl.exe' @('--shutdown') | Out-Null
                Ok 'WSL reiniciado.'
            } else {
                Warn 'A mudanca so vale depois de "wsl --shutdown" (ou de reiniciar o PC).'
            }
        } else {
            Say 'Tudo bem: vamos usar o encaminhamento de portas (portproxy).'
        }
    } else {
        Say 'Este Windows/WSL nao suporta o modo mirrored (precisa de Windows 11 22H2+ e WSL 2.0+).'
        Say 'Vamos usar o encaminhamento de portas (portproxy).'
    }

    if (-not $mode) {
        Say ''
        Say "Encaminhamento: <IP do Windows>:$PortRange -> <IP do WSL>, so nos IPs da rede (nunca 0.0.0.0)."
        Say 'O IP do WSL muda a cada boot; a tarefa agendada \Karaoke\AtualizarRede atualiza sem pedir UAC de novo.'
        if (Ask 'Criar o encaminhamento de portas e a tarefa agendada?' 'S') {
            $wslIp = Get-WslIp
            if (-not $wslIp) {
                Warn 'Nao consegui descobrir o IP do WSL (ip -4 addr show eth0). O WSL esta instalado com uma distro?'
            } elseif ($ips.Count -eq 0) {
                Warn 'Nenhuma rede com gateway no Windows. Conecte ao Wi-Fi e rode de novo: karaoke.bat --configurar-rede'
            } else {
                Enable-IpHelper
                if (Set-KaraokePortProxy $ips $wslIp) { Ok "portproxy $($ips -join ', ') -> $wslIp (portas $PortRange)." }
                else { Warn 'Algumas regras de portproxy falharam (veja: netsh interface portproxy show v4tov4).' }
                Register-RefreshTask
                Ok 'Tarefa agendada \Karaoke\AtualizarRede criada.'
                $mode = 'portproxy'
            }
        }
    }
    if ($mode -ne 'mirrored') { Remove-HyperVRule }   # regra do Hyper-V so faz sentido no mirrored
    if ($mode -eq 'mirrored') {
        # Sobras de uma configuracao antiga em portproxy atrapalhariam o mirrored.
        Remove-KaraokePortProxy
        Unregister-RefreshTask
    }

    Say ''
    $profiles = @()
    if (Ask "Liberar as portas $PortRange/TCP no firewall do Windows (redes Privadas)?" 'S') {
        $profiles = @('Private')
        if (Ask 'Liberar tambem em redes Publicas? (so se voce confia em todos dessa rede)' 'N') { $profiles += 'Public' }
        Set-FirewallRules $profiles ($mode -eq 'mirrored')
    }

    $idx = @($lan | ForEach-Object { $_.InterfaceIndex })
    $public = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue |
            Where-Object { $idx -contains $_.InterfaceIndex -and "$($_.NetworkCategory)" -eq 'Public' })
    foreach ($np in $public) {
        Say ''
        Say "A rede `"$($np.Name)`" ($($np.InterfaceAlias)) esta como Publica: o Windows bloqueia quem chega por ela."
        if (Ask "Marcar a rede `"$($np.Name)`" como Privada?" '') {
            Set-NetConnectionProfile -InterfaceIndex $np.InterfaceIndex -NetworkCategory Private
            Ok "Rede `"$($np.Name)`" marcada como Privada."
        }
    }

    if (-not $mode) { $mode = 'declined' }
    Write-State $mode $profiles $wslChanged $wslExisted
    Write-Log "configure: mode=$mode profiles=$($profiles -join ',') build=$build wsl=$wslVer"

    Say ''
    if ($mode -eq 'declined') {
        Warn 'A rede do WSL nao foi configurada; os celulares provavelmente nao vao conectar.'
        Say  '  Para tentar de novo: karaoke.bat --configurar-rede'
    } else {
        Say "Pronto (modo $mode). Abra o karaoke.bat, ligue o Modo convidado no Controle e escaneie o QR."
    }
    Say 'Para desfazer tudo: karaoke.bat --desfazer-rede'
    return 0
}

function Invoke-Undo {
    Say ''
    Say '=== Karaoke: desfazendo a configuracao de rede ==='
    $state = Read-State
    Remove-FirewallRules
    Ok 'Regras de firewall do Karaoke removidas.'
    Remove-KaraokePortProxy
    Ok "Encaminhamentos (portproxy) das portas $PortRange removidos."
    Unregister-RefreshTask
    Ok 'Tarefa agendada \Karaoke\AtualizarRede removida.'

    Restore-IpHelper

    if ($state -and $state.wslconfig_changed) {
        Say ''
        Say "O modo mirrored foi ligado por este script em $WslConfig."
        if (Ask 'Restaurar o .wslconfig de antes?' '') {
            if ($state.wslconfig_existed -and (Test-Path -LiteralPath $BackupFile)) {
                Copy-Item -LiteralPath $BackupFile -Destination $WslConfig -Force
                Remove-Item -LiteralPath $BackupFile -Force
                Ok '.wslconfig restaurado do backup.'
            } elseif (-not $state.wslconfig_existed) {
                Remove-Item -LiteralPath $WslConfig -Force -ErrorAction SilentlyContinue
                Ok '.wslconfig removido (ele nao existia antes).'
            } else {
                Warn "Backup nao encontrado ($BackupFile); o .wslconfig ficou como esta."
            }
            if (Ask 'Reiniciar o WSL agora (wsl --shutdown)? Todas as distros abertas serao fechadas.' '') {
                Invoke-Native 'wsl.exe' @('--shutdown') | Out-Null
                Ok 'WSL reiniciado.'
            } else {
                Warn 'A mudanca so vale depois de "wsl --shutdown" (ou de reiniciar o PC).'
            }
        } elseif (Test-Path -LiteralPath $BackupFile) {
            Say "  O .wslconfig ficou como esta. O backup continua em $BackupFile."
        }
    }

    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    Say ''
    Say 'O tipo de rede (Privada/Publica) nao foi alterado. Para mudar: Configuracoes > Rede e Internet >'
    Say 'Wi-Fi (ou Ethernet) > propriedades da rede > Tipo de perfil de rede.'
    Say 'Pronto. Na proxima abertura, o karaoke.bat vai perguntar de novo se quer configurar a rede.'
    return 0
}

# ---------------------------------------------------------------- principal

if (-not (Test-Admin)) {
    if ($Refresh) { Write-Log 'refresh: sem privilegio de administrador'; exit 2 }
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Elevated',
        '-UserHome', "`"$UserHome`"", '-UserLocalAppData', "`"$UserLocalAppData`"")
    if ($Undo) { $argList += '-Undo' }
    Say 'Pedindo permissao de administrador (janela do UAC)...'
    try {
        $proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $argList -Verb RunAs -Wait -PassThru
    } catch {
        Warn 'Permissao de administrador negada. Nada foi alterado.'
        Say  '  Para tentar de novo: karaoke.bat --configurar-rede'
        exit 2
    }
    if ($null -eq $proc -or $null -eq $proc.ExitCode) { exit 0 }
    exit $proc.ExitCode
}

$code = 1
try {
    if ($Refresh) { $code = Invoke-Refresh }
    elseif ($Undo) { $code = Invoke-Undo }
    else { $code = Invoke-Configure }
} catch {
    Write-Host "ERRO: $($_.Exception.Message)" -ForegroundColor Red
    Write-Log "erro: $($_.Exception.Message)"
    $code = 1
}
if ($Elevated -and -not $Refresh) { Read-Host 'Pressione Enter para fechar esta janela' | Out-Null }
exit $code
