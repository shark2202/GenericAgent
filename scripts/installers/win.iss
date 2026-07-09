; win.iss — Inno Setup script for GenericAgent (Windows, per-user default).
;
; Built by scripts/build-installer.sh, which passes:
;   /DSourceRoot=<windows path to staging dir containing GenericAgent\>
;   /DAppVersion=<version string>
;   /DAppArch=<win-x64>
; and sets /O (output dir) + /F (output basename).
;
; Install layout (default, per-user, no UAC):
;   %LOCALAPPDATA%\Programs\GenericAgent\   (= {app} = {autopf}\GenericAgent)
;     python\        PBS CPython + deps
;     launch.pyw ... source tree
;     run.cmd/ga.cmd/ga.sh launchers
;     ga.ico         branded icon
;   Start Menu → GenericAgent  (→ pythonw.exe launch.pyw)
;   optional Desktop shortcut
;   {app} added to USER PATH  (so `ga` works in any terminal)
;
; /ALLUSERS=1 on the command line switches to admin/all-users install:
;   {autopf} → Program Files\GenericAgent, PATH → HKLM system PATH, UAC prompts.
;   (PrivilegesRequiredOverridesAllowed=commandline enables this; default is
;   per-user / no UAC.)
;
; First run: launch.pyw copies mykey_template.jsonc → mykey.jsonc itself;
; the installer never prompts for an API key.

#define AppName      "GenericAgent"
#define AppPublisher "GenericAgent"

[Setup]
AppId={{GenericAgent-Install}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
UsePreviousPrivileges=yes
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir=.
OutputBaseFilename={#AppArch}-placeholder
DisableDirPage=no
UninstallDisplayIcon={app}\ga.ico
UninstallDisplayName={#AppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceRoot}\GenericAgent\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "launch.pyw"; WorkingDir: "{app}"; IconFilename: "{app}\ga.ico"; Comment: "Start GenericAgent (GUI)"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Comment: "Remove GenericAgent"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\python\pythonw.exe"; Parameters: "launch.pyw"; WorkingDir: "{app}"; IconFilename: "{app}\ga.ico"; Tasks: desktopicon; Comment: "Start GenericAgent (GUI)"

[Run]
; Let the user opt to launch GA after install.
Filename: "{app}\python\pythonw.exe"; Parameters: "launch.pyw"; WorkingDir: "{app}"; Description: "Launch GenericAgent now"; Flags: postinstall nowait skipifsilent runhidden

[UninstallDelete]
; Remove regenerable runtime cruft the app creates at runtime, but leave
; user data (mykey.jsonc, memory\, .ga_data\) in place for re-install.
Type: filesandordirs; Name: "{app}\temp"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\dist"

[Code]
// ---- PATH management ----
// Add {app} to PATH on install, remove on uninstall. Branch on install mode:
//   per-user (default)  → HKCU\Environment
//   /ALLUSERS=1 (admin) → HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment
// New shells re-read the registry on spawn, so no WM_SETTINGCHANGE broadcast
// is needed for "open a new terminal" to pick up the change.

function GetPathRootAndSubKey(out RootKey: Integer; out SubKey: String): Boolean;
begin
  if IsAdminInstallMode then begin
    RootKey := HKEY_LOCAL_MACHINE;
    SubKey := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment';
  end else begin
    RootKey := HKEY_CURRENT_USER;
    SubKey := 'Environment';
  end;
  Result := True;
end;

function PathContainsApp(const Path, AppDir: String): Boolean;
begin
  Result := (Pos(AppDir + ';', Path) > 0) or
            (Pos(';' + AppDir, Path) > 0) or
            (Path = AppDir);
end;

procedure AddAppToPath;
var
  RootKey: Integer; SubKey, Path, AppDir: String;
begin
  AppDir := ExpandConstant('{app}');
  GetPathRootAndSubKey(RootKey, SubKey);
  if not RegQueryStringValue(RootKey, SubKey, 'Path', Path) then
    Path := '';
  if not PathContainsApp(Path, AppDir) then begin
    if (Path <> '') and (Path[Length(Path)] <> ';') then
      Path := Path + ';';
    Path := Path + AppDir;
    RegWriteExStringValue(RootKey, SubKey, 'Path', Path);
  end;
end;

procedure RemoveAppFromPath;
var
  RootKey: Integer; SubKey, Path, AppDir: String;
  P: Integer;
begin
  AppDir := ExpandConstant('{app}');
  GetPathRootAndSubKey(RootKey, SubKey);
  if not RegQueryStringValue(RootKey, SubKey, 'Path', Path) then
    Exit;
  // Remove "AppDir;" anywhere, or a trailing "AppDir" or ";AppDir".
  P := Pos(AppDir + ';', Path);
  if P > 0 then
    Delete(Path, P, Length(AppDir) + 1)
  else if Pos(';' + AppDir, Path) > 0 then begin
    P := Pos(';' + AppDir, Path);
    Delete(Path, P, Length(AppDir) + 1);
  end else if Path = AppDir then
    Path := '';
  RegWriteExStringValue(RootKey, SubKey, 'Path', Path);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    AddAppToPath;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RemoveAppFromPath;
end;
