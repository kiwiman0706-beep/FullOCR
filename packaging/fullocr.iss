; Inno Setup script for FullOCR (Windows installer).
;
; Produces a single FullOCR-Setup.exe that installs the PyInstaller onedir
; build (dist\fullocr-gui\, with the NDLOCR-Lite companion already assembled
; inside it) to a per-user, writable location and creates shortcuts.
;
; Update-in-place: AppId is a FIXED GUID. Re-running a newer setup detects the
; existing install (same AppId), removes the old files, and writes the new
; ones to the same folder — an in-place upgrade, no uninstall-first needed.
; ignoreversion on [Files] makes every file overwrite regardless of file
; version/timestamp (PyInstaller artifacts don't carry meaningful versions).
;
; Built by .github/workflows/build-windows.yml, which passes the app version:
;   ISCC /DMyAppVersion=1.2.3 packaging\fullocr.iss
; (falls back to 0.0.0 for local/manual builds).

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

#define MyAppName "Theta.FullOCR"
#define MyAppExeName "fullocr-gui.exe"
#define MyAppPublisher "Theta"

[Setup]
; Keep this GUID stable forever — it is what makes reinstalls upgrade in place.
AppId={{9C6B7F4E-2D3A-4F1B-9E8C-5A7D0B1C2E3F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install: no admin rights needed, lands in a writable location.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Inno resolves relative [Files] Source and OutputDir paths against SourceDir,
; which (being relative) is itself resolved against this script's folder
; (packaging\). ".." makes the repo root the base, so "dist\fullocr-gui\*"
; below points at the PyInstaller output, and the installer lands in repo\installer\.
SourceDir=..
; Setup executable name (what users download and double-click).
OutputBaseFilename=FullOCR-Setup
OutputDir=installer
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Show the version in the wizard title bar.
AppVerName={#MyAppName} {#MyAppVersion}
; Reinstalling on top of an existing install upgrades in place (same AppId),
; so don't nag with the "already installed" prompt.
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole onedir tree (app + _internal + bundled ndlocr-lite companion).
; recursesubdirs pulls the entire tree; ignoreversion forces overwrite on
; upgrade so stale DLLs/models from an older version never linger.
Source: "dist\fullocr-gui\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Offer to launch right after install finishes.
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
