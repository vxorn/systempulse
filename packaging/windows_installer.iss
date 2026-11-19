#ifndef AppVersion
  #error AppVersion is required
#endif

[Setup]
AppId={{18840801-D96F-43ED-9FD7-CF3F50220EA1}
AppName=SystemPulse
AppVersion={#AppVersion}
AppPublisher=SystemPulse contributors
AppPublisherURL=https://github.com/vxorn/systempulse
DefaultDirName={localappdata}\Programs\SystemPulse
DefaultGroupName=SystemPulse
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\SystemPulse.exe
LicenseFile=..\LICENSE
OutputDir=..\installer
OutputBaseFilename=SystemPulse-Setup-Windows-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\SystemPulseDesktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\SystemPulse"; Filename: "{app}\SystemPulse.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\SystemPulse.exe"; Description: "SystemPulse"; Flags: nowait postinstall skipifsilent
