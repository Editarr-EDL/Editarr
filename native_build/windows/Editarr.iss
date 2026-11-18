#ifndef StageRoot
  #error StageRoot is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef AppPublisher
  #error AppPublisher is required
#endif
#ifndef AppId
  #error AppId is required
#endif
#ifndef AppGuid
  #error AppGuid is required
#endif
#ifndef AppDisplayName
  #error AppDisplayName is required
#endif
#ifndef ArtifactBaseFilename
  #error ArtifactBaseFilename is required
#endif
#ifndef BuildDescription
  #error BuildDescription is required
#endif

[Setup]
AppId={#AppId}
AppName={#AppDisplayName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppVerName={#AppDisplayName} {#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultDirName={localappdata}\Programs\Editarr
DefaultGroupName=Editarr
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename={#ArtifactBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
PrivilegesRequired=lowest
CloseApplications=no
RestartApplications=no
SetupIconFile={#StageRoot}\Editarr.ico
UninstallDisplayIcon={app}\Editarr.exe
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#BuildDescription}
VersionInfoProductName=Editarr
VersionInfoProductVersion={#AppVersion}
WizardStyle=modern

[Tasks]
Name: "autostart"; Description: "Launch Editarr when I sign in"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
Source: "{#StageRoot}\Editarr.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\Editarr.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#StageRoot}\Runtime\*"; DestDir: "{app}\Runtime"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageRoot}\Licenses\*"; DestDir: "{app}\Licenses"; Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

[Icons]
Name: "{group}\Editarr"; Filename: "{app}\Editarr.exe"; WorkingDir: "{app}"
Name: "{group}\Editarr Diagnostics"; Filename: "{app}\Runtime\Editarr.exe"; Parameters: "--foreground"; WorkingDir: "{app}\Runtime"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Editarr"; ValueData: """{app}\Editarr.exe"" --background"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\Editarr.exe"; Description: "Launch Editarr"; Flags: nowait postinstall skipifsilent

[Code]
const
  UninstallKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppGuid}_is1';

function VersionPart(var Value: String): Integer;
var
  Position: Integer;
  Part: String;
begin
  Position := Pos('.', Value);
  if Position = 0 then begin
    Part := Value;
    Value := '';
  end else begin
    Part := Copy(Value, 1, Position - 1);
    Delete(Value, 1, Position);
  end;
  Result := StrToIntDef(Part, 0);
end;

function CompareVersions(Left, Right: String): Integer;
var
  Index: Integer;
  LeftPart, RightPart: Integer;
begin
  Result := 0;
  for Index := 1 to 4 do begin
    LeftPart := VersionPart(Left);
    RightPart := VersionPart(Right);
    if LeftPart < RightPart then begin Result := -1; Exit; end;
    if LeftPart > RightPart then begin Result := 1; Exit; end;
  end;
end;

function InitializeSetup(): Boolean;
var
  ExistingVersion: String;
begin
  Result := True;
  if RegQueryStringValue(HKCU, UninstallKey, 'DisplayVersion', ExistingVersion) and
     (CompareVersions('{#AppVersion}', ExistingVersion) < 0) then begin
    SuppressibleMsgBox(
      'A newer Editarr version (' + ExistingVersion + ') is already installed.',
      mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\Editarr.exe')) then begin
    if not Exec(ExpandConstant('{app}\Editarr.exe'), '--quit', '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) then
      Result := 'Unable to run the existing Editarr launcher. Quit Editarr and retry the upgrade.'
    else if ResultCode <> 0 then
      Result := 'Unable to stop the existing Editarr instance. Quit Editarr and retry the upgrade.';
  end;
end;
