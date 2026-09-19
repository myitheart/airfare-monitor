#define MyAppName "航价守望"
#define MyAppVersion "0.6.18"
#define MyAppPublisher "AirfareMonitor"
#define MyAppExeName "AirfareMonitor.exe"
#ifndef BuildRoot
  #define BuildRoot "..\dist\AirfareMonitor"
#endif
#ifndef OutputRoot
  #define OutputRoot "..\release"
#endif

[Setup]
AppId={{B2D23D48-88C7-4C64-94C0-B827A397537E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AirfareMonitor
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=AirfareMonitorSetup-{#MyAppVersion}
OutputDir={#OutputRoot}
Compression=lzma
SolidCompression=yes
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\resources\app-icon-{#MyAppVersion}.ico
SetupIconFile=..\resources\app.ico
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\resources\app.ico"; DestDir: "{app}\resources"; DestName: "app-icon-{#MyAppVersion}.ico"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\resources\app-icon-{#MyAppVersion}.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\resources\app-icon-{#MyAppVersion}.ico"; Check: ShouldCreateDesktopIcon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function ShouldCreateDesktopIcon(): Boolean;
begin
  { Refresh an existing shortcut on upgrade even if the task checkbox is not
    shown as selected. New installs still respect the user's task choice. }
  Result := WizardIsTaskSelected('desktopicon') or
    FileExists(ExpandConstant('{autodesktop}\{#MyAppName}.lnk'));
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  if not UninstallSilent then
    MsgBox('卸载默认保留本地航程、历史、Excel、日志和独立浏览器 Profile。', mbInformation, MB_OK);
end;
