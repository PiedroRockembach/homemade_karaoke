@echo off
rem Abre o karaoke com um clique no Windows (via WSL): instala na 1a vez e inicia.
rem   karaoke.bat                    instala se preciso e inicia
rem   karaoke.bat --reinstalar       forca o make install antes de iniciar
rem   karaoke.bat --criar-atalho     cria o atalho na area de trabalho
rem   karaoke.bat --configurar-rede  libera a rede/firewall para os celulares dos convidados (pede UAC)
rem   karaoke.bat --desfazer-rede    desfaz o --configurar-rede (pede UAC)
rem O projeto so roda em Linux, entao tudo acontece dentro do WSL (wsl --install).
rem Rede dos convidados: docs/adr/0006-windows-wsl-lan-and-firewall.md
setlocal
chcp 65001 >nul
title Karaoke
cd /d "%~dp0"

set "REDE_PS1=%~dp0scripts\windows\setup-network.ps1"
set "REDE_DIR=%LOCALAPPDATA%\Karaoke"
set "REDE_ESTADO=%LOCALAPPDATA%\Karaoke\rede.json"

if /i "%~1"=="--criar-atalho" goto :atalho
if /i "%~1"=="--configurar-rede" goto :configurar_rede
if /i "%~1"=="--desfazer-rede" goto :desfazer_rede

where wsl >nul 2>nul
if errorlevel 1 (
  echo ERRO: WSL nao encontrado. Instale com: wsl --install   ^(e reinicie o PC^)
  goto :falha
)

wsl --cd "%~dp0." -- sh -c "command -v make >/dev/null && command -v python3 >/dev/null"
if errorlevel 1 (
  echo ERRO: dentro do WSL faltam make e/ou python3. No Ubuntu:
  echo   wsl -- sudo apt install make python3 python3-venv ffmpeg
  goto :falha
)

set "INSTALAR="
if /i "%~1"=="--reinstalar" set "INSTALAR=1"
wsl --cd "%~dp0." -- test -x .venv/bin/python
if errorlevel 1 set "INSTALAR=1"
if defined INSTALAR (
  echo ^>^> Primeira execucao: instalando ^(pode levar alguns minutos^)...
  wsl --cd "%~dp0." -- make install
  if errorlevel 1 (
    echo ERRO: a instalacao falhou. Veja as mensagens acima.
    goto :falha
  )
)

rem Rede dos convidados: pergunta uma vez (some quando o estado existe, inclusive "declined").
if not exist "%REDE_ESTADO%" call :oferecer_rede
rem No modo portproxy o IP do WSL muda a cada boot: a tarefa elevada reaplica sem novo UAC.
findstr /i /c:"portproxy" "%REDE_ESTADO%" >nul 2>nul
if not errorlevel 1 (
  schtasks /run /tn "\Karaoke\AtualizarRede" >nul 2>nul
  if errorlevel 1 echo AVISO: tarefa \Karaoke\AtualizarRede nao encontrada; os celulares podem nao conectar. Rode: karaoke.bat --configurar-rede
)

rem O navegador abre pelo Windows assim que o servidor responder.
set "KARAOKE_NO_BROWSER=1"
set "WSLENV=KARAOKE_NO_BROWSER"
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 180;$i++){try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 http://127.0.0.1:8765/ | Out-Null; Start-Process 'http://127.0.0.1:8765/'; break}catch{Start-Sleep 1}}"

wsl --cd "%~dp0." -- make start
if errorlevel 1 (
  echo O karaoke encerrou com erro.
  goto :falha
)
exit /b 0

:oferecer_rede
echo.
echo Para os celulares dos convidados abrirem o QR, o Windows precisa liberar a rede para o WSL
echo ^(firewall e encaminhamento de portas 8765-8774^). Isso pede permissao de administrador ^(UAC^).
choice /c SN /m "Configurar agora a rede para os celulares dos convidados"
if errorlevel 2 (
  if not exist "%REDE_DIR%" mkdir "%REDE_DIR%"
  >"%REDE_ESTADO%" echo {"mode": "declined", "ports": [8765, 8774]}
  echo Tudo bem. Para configurar depois: karaoke.bat --configurar-rede
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%REDE_PS1%"
if errorlevel 2 (
  rem UAC negado: lembra a recusa para nao perguntar a cada abertura.
  if not exist "%REDE_DIR%" mkdir "%REDE_DIR%"
  >"%REDE_ESTADO%" echo {"mode": "declined", "reason": "uac", "ports": [8765, 8774]}
  echo AVISO: permissao de administrador negada; a rede nao foi configurada. O karaoke abre mesmo assim.
  echo        Para configurar depois: karaoke.bat --configurar-rede
  exit /b 0
)
if errorlevel 1 echo AVISO: a rede nao foi configurada. O karaoke abre mesmo assim; para tentar de novo: karaoke.bat --configurar-rede
exit /b 0

:configurar_rede
powershell -NoProfile -ExecutionPolicy Bypass -File "%REDE_PS1%"
goto :rede_fim

:desfazer_rede
powershell -NoProfile -ExecutionPolicy Bypass -File "%REDE_PS1%" -Undo

:rede_fim
set "RC=%errorlevel%"
if not "%RC%"=="0" echo AVISO: o script de rede terminou com codigo %RC% ^(2 = permissao de administrador negada^).
pause
exit /b %RC%

:atalho
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Karaoke.lnk'); $s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\imageres.dll,103'; $s.Save()"
if errorlevel 1 goto :falha
echo Atalho criado na area de trabalho: Karaoke.lnk
pause
exit /b 0

:falha
pause
exit /b 1
