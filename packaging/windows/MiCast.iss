#define AppName "MiCast"
#ifndef AppVersion
  #define AppVersion "0.2.1"
#endif

[Setup]
AppId={{83B8FA25-613D-48DC-88E5-0A747D2A2FCB}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={autopf}\MiCast
DefaultGroupName=MiCast
OutputDir=..\..\dist\installer
OutputBaseFilename=MiCast-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\MiCast.exe

[Files]
Source: "..\..\dist\MiCast.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MiCast"; Filename: "{app}\MiCast.exe"
Name: "{autodesktop}\MiCast"; Filename: "{app}\MiCast.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Run]
Filename: "{app}\MiCast.exe"; Description: "启动 MiCast"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
begin
  Choice := MsgBox(
    '是否同时删除 MiCast 设置、音箱配置和米家登录信息？' + #13#10 + #13#10 +
    '选择“否”将保留配置，重新安装后可以继续使用。' + #13#10 + #13#10 +
    '注意：删除操作只影响当前 Windows 账户的数据目录；' +
    '其他账户下的配置需要登录对应账户后手动删除 %APPDATA%\MiCast。',
    mbConfirmation, MB_YESNOCANCEL);
  if Choice = IDCANCEL then begin
    Result := False;
    exit;
  end;
  RemoveUserData := Choice = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then begin
    DelTree(ExpandConstant('{localappdata}\MiCast'), True, True, True);
    if RemoveUserData then
      DelTree(ExpandConstant('{userappdata}\MiCast'), True, True, True);
  end;
end;
