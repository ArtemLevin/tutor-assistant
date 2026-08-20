#ifndef AppVersion
  #define AppVersion "1.0.0rc1"
#endif
#ifndef BuildDirectory
  #define BuildDirectory "..\..\dist\TutorAssistant"
#endif
#ifndef OutputDirectory
  #define OutputDirectory "..\..\dist"
#endif

[Setup]
AppId={{D06C9DCC-2587-47C1-BD6F-B5F68CE2DF17}
AppName=Tutor Assistant
AppVersion={#AppVersion}
AppPublisher=Артём Александрович Лёвин
DefaultDirName={localappdata}\Programs\TutorAssistant
DefaultGroupName=Tutor Assistant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDirectory}
OutputBaseFilename=TutorAssistant-{#AppVersion}-win64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\TutorAssistant.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#BuildDirectory}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "portable.mode"

[Icons]
Name: "{autoprograms}\Tutor Assistant"; Filename: "{app}\TutorAssistant.exe"
Name: "{autodesktop}\Tutor Assistant"; Filename: "{app}\TutorAssistant.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TutorAssistant.exe"; Description: "Launch Tutor Assistant"; Flags: nowait postinstall skipifsilent

; User configuration, workspace and backups are outside {app}. No [UninstallDelete]
; entry may target {userappdata}, {localappdata}\TutorAssistant, or a custom workspace.
