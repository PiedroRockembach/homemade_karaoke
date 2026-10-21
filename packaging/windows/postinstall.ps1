# Pos-instalacao do Karaoke no Windows (chamado pelo instalador; pode ser rodado de novo a qualquer momento).
#  1. confere o WSL (sem WSL: explica e oferece "wsl --install", que pede administrador e reinicio);
#  2. instala make, python3, python3-venv e ffmpeg dentro do WSL como root (sem pedir senha);
#  3. oferece a 1a instalacao (~7 GB) com "karaoke.bat --reinstalar", que tambem abre o karaoke.
# A rede para os celulares dos convidados e oferecida pelo karaoke.bat na 1a abertura (ADR-0006).
param([string]$AppDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path)
$ErrorActionPreference = 'Continue'
$env:WSL_UTF8 = '1'

function Perguntar([string]$Texto) {
  $r = Read-Host "$Texto [S/n]"
  return ($r -eq '' -or $r -match '^[sSyY]')
}
function Fim([int]$Codigo) {
  Read-Host 'Aperte Enter para fechar' | Out-Null
  exit $Codigo
}

Write-Host '== Karaoke: preparando o WSL ==' -ForegroundColor Cyan
$temWsl = $false
if (Get-Command wsl.exe -ErrorAction SilentlyContinue) {
  & wsl.exe -e true 2>$null | Out-Null
  $temWsl = ($LASTEXITCODE -eq 0)
}
if (-not $temWsl) {
  Write-Host 'O karaoke roda dentro do WSL (Linux no Windows), e nao achei o WSL com uma distribuicao Linux.'
  if (Perguntar 'Instalar agora o WSL com Ubuntu? (pede administrador; depois reinicie o PC)') {
    try {
      Start-Process wsl.exe -ArgumentList '--install', '-d', 'Ubuntu' -Verb RunAs -Wait
      Write-Host ''
      Write-Host 'Reinicie o PC, abra o "Ubuntu" no menu Iniciar uma vez para criar seu usuario e'
      Write-Host 'depois clique no atalho "Karaoke" da area de trabalho: ele termina a instalacao.'
      Write-Host 'Para refazer esta preparacao: menu Iniciar > Karaoke (ou rode este script de novo).'
    } catch {
      Write-Warning 'Permissao de administrador negada. Para instalar depois: abra o PowerShell como administrador e rode  wsl --install'
    }
  } else {
    Write-Host 'Para instalar depois: PowerShell como administrador ->  wsl --install  (e reinicie o PC).'
  }
  Fim 0
}

& wsl.exe -u root -- sh -c 'command -v apt-get >/dev/null'
if ($LASTEXITCODE -ne 0) {
  Write-Warning 'Sua distribuicao do WSL nao usa apt. Instale nela: make, python3 (>= 3.12), venv e ffmpeg.'
} else {
  Write-Host '>> Instalando make, python3, python3-venv e ffmpeg no WSL (pode levar alguns minutos)...'
  & wsl.exe -u root -- sh -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update -q && apt-get install -y -q make python3 python3-venv ffmpeg'
  if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Falhou a instalacao dos pacotes no WSL. Tente no Ubuntu:  sudo apt install make python3 python3-venv ffmpeg'
  }
}

& wsl.exe -e python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) {
  Write-Warning 'O Python do WSL e mais antigo que 3.12. Use o Ubuntu 24.04 ou mais novo:  wsl --install -d Ubuntu-24.04'
  Fim 1
}

$bat = Join-Path $AppDir 'karaoke.bat'
Write-Host ''
Write-Host 'A primeira instalacao baixa ~7 GB (PyTorch, Demucs, Whisper) e leva de 5 a 30 minutos.'
if (Perguntar 'Fazer agora e abrir o karaoke no final?') {
  Start-Process -FilePath $bat -ArgumentList '--reinstalar' -WorkingDirectory $AppDir
} else {
  Write-Host 'Tudo bem: o atalho "Karaoke" da area de trabalho faz isso na primeira vez que for aberto.'
  Fim 0
}
