# Gera dist\karaoke-<versao>-windows-setup.exe com o Inno Setup 6 (no Windows, com Python 3.11+ e Pillow).
#   $env:KARAOKE_BUILD = 7   (opcional, padrao 0)  ->  versao <version do pyproject>-build.7
#   pwsh packaging\build-windows.ps1
# Clone com fim de linha LF (git config core.autocrlf false): os arquivos rodam dentro do WSL.
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$py = if ($env:PYTHON) { $env:PYTHON } else { 'python' }
$build = if ($env:KARAOKE_BUILD) { $env:KARAOKE_BUILD } else { '0' }
$version = (& $py packaging/stage.py --version)
if ($LASTEXITCODE -ne 0 -or -not $version) { throw 'stage.py --version falhou' }
$version = $version.Trim()
$full = "$version-build.$build"
$out = Join-Path $Root 'dist'
$work = Join-Path $out 'work-windows'
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
New-Item -ItemType Directory -Force -Path $work | Out-Null

& $py packaging/stage.py (Join-Path $work 'karaoke') $full
if ($LASTEXITCODE -ne 0) { throw 'stage.py falhou' }
$ico = Join-Path $work 'karaoke.ico'
& $py packaging/windows/make_ico.py karaoke_icon.png $ico
if ($LASTEXITCODE -ne 0) { throw 'make_ico.py falhou (pip install pillow)' }

$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { throw 'Inno Setup 6 nao encontrado (choco install innosetup)' }
& $iscc /Q "/DAppVersion=$full" "/DAppNumVersion=$version.$build" "/DStageDir=$(Join-Path $work 'karaoke')" `
  "/DIconFile=$ico" "/DOutDir=$out" "/DOutBase=karaoke-$full-windows-setup" 'packaging\windows\karaoke.iss'
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou ($LASTEXITCODE)" }
Write-Host ">> $(Join-Path $out "karaoke-$full-windows-setup.exe")"
