; Instalador do Karaoke para Windows (Inno Setup 6). Gerado por packaging/build-windows.ps1:
;   ISCC /DAppVersion=0.4.0-build.7 /DAppNumVersion=0.4.0.7 /DStageDir=<pasta da app> /DIconFile=<.ico>
;        /DOutDir=<saida> /DOutBase=karaoke-0.4.0-build.7-windows-setup karaoke.iss
; Instala por usuario (sem administrador) em %LOCALAPPDATA%\Programs\Karaoke. A app roda dentro do WSL
; (karaoke.bat); o .venv e os dados (data\) sao criados la na 1a abertura e sobrevivem a atualizacoes.
; Texto so em ASCII de proposito (o .bat tambem): evita problema de codificacao no compilador.

#ifndef AppVersion
  #define AppVersion "0.0.0-build.0"
#endif
#ifndef AppNumVersion
  #define AppNumVersion "0.0.0.0"
#endif
#ifndef OutBase
  #define OutBase "karaoke-windows-setup"
#endif

[Setup]
AppId={{8C5D3F7A-2B1E-4C6D-9A0F-5E7B3C1D2A4F}
AppName=Karaoke
AppVersion={#AppVersion}
AppVerName=Karaoke {#AppVersion}
VersionInfoVersion={#AppNumVersion}
AppPublisher=homemade_karaoke
AppPublisherURL=https://github.com/PiedroRockembach/homemade_karaoke
AppSupportURL=https://github.com/PiedroRockembach/homemade_karaoke/issues
DefaultDirName={localappdata}\Programs\Karaoke
DefaultGroupName=Karaoke
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutDir}
OutputBaseFilename={#OutBase}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\karaoke.ico
UninstallDisplayName=Karaoke
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Languages]
Name: "ptbr"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"

[InstallDelete]
; Atualizacao: troca o codigo da app sem tocar em .venv\ nem em data\.
Type: filesandordirs; Name: "{app}\karaoke"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\scripts"

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#IconFile}"; DestDir: "{app}"; DestName: "karaoke.ico"; Flags: ignoreversion
Source: "postinstall.ps1"; DestDir: "{app}\scripts\windows"; Flags: ignoreversion

[UninstallDelete]
; Residuos criados depois da instalacao (pip install -e, __pycache__, caches). .venv e data\ ficam no [Code].
Type: filesandordirs; Name: "{app}\karaoke"
Type: filesandordirs; Name: "{app}\docs"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\*.egg-info"
Type: filesandordirs; Name: "{app}\.pytest_cache"
Type: filesandordirs; Name: "{app}\.ruff_cache"

[Icons]
Name: "{userdesktop}\Karaoke"; Filename: "{app}\karaoke.bat"; WorkingDir: "{app}"; IconFilename: "{app}\karaoke.ico"; Comment: "Karaoke local (YouTube + letra sincronizada)"; Tasks: desktopicon
Name: "{group}\Karaoke"; Filename: "{app}\karaoke.bat"; WorkingDir: "{app}"; IconFilename: "{app}\karaoke.ico"; Comment: "Karaoke local (YouTube + letra sincronizada)"
Name: "{group}\Configurar rede dos convidados"; Filename: "{app}\karaoke.bat"; Parameters: "--configurar-rede"; WorkingDir: "{app}"; IconFilename: "{app}\karaoke.ico"
Name: "{group}\Desinstalar Karaoke"; Filename: "{uninstallexe}"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\windows\postinstall.ps1"" -AppDir ""{app}"""; WorkingDir: "{app}"; Description: "Preparar o WSL e fazer a primeira instalacao (recomendado)"; Flags: postinstall waituntilterminated skipifsilent

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir, Rede: string;
  Estado: AnsiString;
  RC: Integer;
begin
  AppDir := ExpandConstant('{app}');
  if CurUninstallStep = usUninstall then
  begin
    { Rede dos convidados (ADR-0006): desfaz firewall/portproxy se tiver sido configurada. }
    Rede := ExpandConstant('{localappdata}\Karaoke\rede.json');
    if (not UninstallSilent) and FileExists(Rede) and LoadStringFromFile(Rede, Estado)
       and (Pos('declined', String(Estado)) = 0) then
      if MsgBox('Desfazer a configuracao de rede/firewall feita para os convidados?' + #13#10 +
                '(pede permissao de administrador)', mbConfirmation, MB_YESNO) = IDYES then
        Exec(ExpandConstant('{cmd}'), '/c ""' + AppDir + '\karaoke.bat" --desfazer-rede"', AppDir,
             SW_SHOW, ewWaitUntilTerminated, RC);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    { O .venv (~7 GB) nao e dado do usuario: remove. Os links do WSL saem melhor pelo proprio WSL. }
    if DirExists(AppDir + '\.venv') then
    begin
      Exec('wsl.exe', '--cd "' + AppDir + '" -- rm -rf .venv', AppDir, SW_HIDE, ewWaitUntilTerminated, RC);
      DelTree(AppDir + '\.venv', True, True, True);
    end;
    DelTree(AppDir + '\.pytest_cache', True, True, True);
    if DirExists(AppDir + '\data') then
    begin
      if (not UninstallSilent) and (MsgBox('Apagar tambem suas musicas e dados em ' + AppDir + '\data ?' + #13#10 +
          'Isso nao pode ser desfeito. Escolha Nao para guardar (faca backup da pasta).',
          mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES) then
        DelTree(AppDir + '\data', True, True, True);
    end;
    RemoveDir(AppDir);
  end;
end;
