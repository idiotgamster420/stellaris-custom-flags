; Inno Setup script for StellarisFlagUploader-Setup.exe. Build after PyInstaller with:
;   iscc /DAppVersion=1.0.0 packaging\windows\installer.iss
; Installs for the current user only, so it needs no administrator rights.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0B3C9E-4D1A-4B7E-9C53-2E8A51F7D204}
AppName=Stellaris Flag Uploader
AppVersion={#AppVersion}
AppVerName=Stellaris Flag Uploader {#AppVersion}
AppPublisher=idiotgamster420
AppPublisherURL=https://github.com/idiotgamster420/stellaris-custom-flags
AppSupportURL=https://github.com/idiotgamster420/stellaris-custom-flags/issues
DefaultDirName={autopf}\Stellaris Flag Uploader
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\build\windows
OutputBaseFilename=StellarisFlagUploader-Setup
SetupIconFile=..\..\app\icon.ico
UninstallDisplayIcon={app}\StellarisFlagUploader.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Files]
Source: "..\..\build\windows\dist\StellarisFlagUploader\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Stellaris Flag Uploader"; Filename: "{app}\StellarisFlagUploader.exe"
Name: "{autodesktop}\Stellaris Flag Uploader"; Filename: "{app}\StellarisFlagUploader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\StellarisFlagUploader.exe"; Description: "Open Stellaris Flag Uploader"; Flags: nowait postinstall skipifsilent
