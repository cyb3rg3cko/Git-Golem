; Inno Setup script for Git Project Manager.
; Built by packaging\build_windows.ps1, which passes /DAppVersion=x.y.z
; Paths are relative to this file (packaging\).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Git Project Manager"
#define AppExe "GitProjectManager.exe"

[Setup]
AppId={{8F3C2A1E-5B7D-4E2A-9C61-2D4F8A7B9E10}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\build\icon.ico
OutputDir=..\dist
OutputBaseFilename=GitProjectManager-{#AppVersion}-windows-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installs for the current user without admin rights; the user can choose "all users" instead.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  Code: Integer;
begin
  Result := True;
  { The app runs git commands, so let the user know if Git isn't installed yet. }
  if not Exec(ExpandConstant('{cmd}'), '/C where git', '', SW_HIDE, ewWaitUntilTerminated, Code) or (Code <> 0) then
    MsgBox('Git was not found on this computer.' + #13#10 + #13#10 +
           'Git Project Manager needs Git to work. You can install it from https://git-scm.com ' +
           'before or after this setup.', mbInformation, MB_OK);
end;
