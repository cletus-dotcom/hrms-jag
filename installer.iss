; Inno Setup script for HRMS Windows Installer
; Uses NSSM to install hrms.exe as a Windows service (auto-start when PC starts)
;
; Prerequisites before building:
;   1. Build the exe: pyinstaller hrms.spec  (creates dist\hrms.exe)
;   2. Download NSSM from https://nssm.cc/download and place:
;        nssm\win64\nssm.exe  (for 64-bit installer)
;        nssm\win32\nssm.exe  (for 32-bit installer, optional)
; Build installer: iscc installer.iss

#define MyAppName "HRMS"
#define MyAppVersion "1.3"
#define MyAppPublisher "HRMS"
#define MyAppExeName "hrms.exe"
#define MyAppServiceName "HRMS"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Need admin to install/remove Windows service
PrivilegesRequired=admin
OutputDir=installer_output
OutputBaseFilename=HRMS_Setup_{#MyAppVersion}
SetupIconFile=
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "installservice"; Description: "Install HRMS as a Windows service (auto-start with Windows)"; GroupDescription: "Service"; Flags: checkedonce
Name: "startservice"; Description: "Start the HRMS service after installation"; GroupDescription: "Service"; Flags: unchecked

[Files]
; Main application (build with: pyinstaller hrms.spec)
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; NSSM - download from https://nssm.cc/download and place nssm\win64\nssm.exe before building (64-bit installer)
Source: "nssm\win64\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; Install Windows service via NSSM (only if task selected)
Filename: "{app}\nssm.exe"; Parameters: "install {#MyAppServiceName} ""{app}\{#MyAppExeName}"""; StatusMsg: "Installing HRMS service..."; Flags: runhidden waituntilterminated; Tasks: installservice
Filename: "{app}\nssm.exe"; Parameters: "set {#MyAppServiceName} AppDirectory ""{app}"""; StatusMsg: "Configuring service..."; Flags: runhidden waituntilterminated; Tasks: installservice
Filename: "{app}\nssm.exe"; Parameters: "set {#MyAppServiceName} Start SERVICE_AUTO_START"; StatusMsg: "Setting auto-start..."; Flags: runhidden waituntilterminated; Tasks: installservice
Filename: "{app}\nssm.exe"; Parameters: "start {#MyAppServiceName}"; StatusMsg: "Starting HRMS service..."; Flags: runhidden waituntilterminated; Tasks: startservice

[UninstallRun]
; Stop and remove Windows service on uninstall (no-op if service was not installed)
Filename: "{app}\nssm.exe"; Parameters: "stop {#MyAppServiceName}"; Flags: runhidden waituntilterminated; RunOnceId: "StopHRMSService"
Filename: "{app}\nssm.exe"; Parameters: "remove {#MyAppServiceName} confirm"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveHRMSService"
