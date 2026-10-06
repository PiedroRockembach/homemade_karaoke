@echo off
rem Abre o karaoke com um clique no Windows (via WSL): instala na 1a vez e inicia.
rem   karaoke.bat                 instala se preciso e inicia
rem   karaoke.bat --reinstalar    forca o make install antes de iniciar
rem   karaoke.bat --criar-atalho  cria o atalho na area de trabalho
rem O projeto so roda em Linux, entao tudo acontece dentro do WSL (wsl --install).
setlocal
chcp 65001 >nul
title Karaoke
cd /d "%~dp0"

if /i "%~1"=="--criar-atalho" goto :atalho

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

:atalho
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Karaoke.lnk'); $s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\imageres.dll,103'; $s.Save()"
if errorlevel 1 goto :falha
echo Atalho criado na area de trabalho: Karaoke.lnk
pause
exit /b 0

:falha
pause
exit /b 1
