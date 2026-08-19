; Скрипт Inno Setup для «Парламента». Собирает build/windows (папку из
; flet build windows — она всегда так выглядит, DLL и служебные файлы
; никуда не деть) в один инсталлятор ParlamentSetup.exe: пользователь видит
; только его, всё остальное ставится в Program Files и прячется за
; ярлыком в меню «Пуск» — и на рабочий стол по желанию.
;
; MyAppExeName подставляется снаружи через `iscc /DMyAppExeName=...`
; (см. .github/workflows/build-windows.yml) — имя exe внутри build/windows
; определяется на месте, а не жёстко зашито здесь.
#ifndef MyAppExeName
  #define MyAppExeName "parlament.exe"
#endif

#define MyAppName "Парламент"
#define MyAppPublisher "Parlament"
#define MyAppVersion "1.0.0"

[Setup]
AppId={{03774BCC-9761-4820-B813-761454A5ABE9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=dist
OutputBaseFilename=ParlamentSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
Source: "..\..\build\windows\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить «{#MyAppName}»"; Flags: nowait postinstall skipifsilent
