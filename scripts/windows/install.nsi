Unicode true
!include "MUI2.nsh"
!include "x64.nsh"
!ifndef BuildDir
  !error "Define BuildDir"
!endif
!ifndef OutputFile
  !error "Define OutputFile"
!endif
!ifndef AppVersion
  !error "Define AppVersion"
!endif
Name "CPU Memory Cleanup"
OutFile "${OutputFile}"
InstallDir "$LOCALAPPDATA\Programs\DreamSoul\CPU Memory Cleanup"
RequestExecutionLevel user
SetCompressor /SOLID lzma
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "This package requires Windows x64."
    Abort
  ${EndIf}
FunctionEnd
Section "Install"
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File "${BuildDir}\memory-cleanup.exe"
  File "${BuildDir}\cpu-memory-cleanup.exe"
  File "..\..\LICENSE"
!ifdef WindowBuildDir
  File "${WindowBuildDir}\memory-cleanup-window.exe"
  File "${WindowBuildDir}\WebView2Loader.dll"
!endif
  IfErrors 0 +3
    MessageBox MB_ICONSTOP "Close the running service before upgrading. Installation could not finish."
    Abort
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\CPU Memory Cleanup"
!ifdef WindowBuildDir
  CreateShortcut "$SMPROGRAMS\CPU Memory Cleanup\CPU Memory Cleanup.lnk" "$INSTDIR\memory-cleanup-window.exe" '--output-dir "$LOCALAPPDATA\DreamSoul\CPU Memory Cleanup\runs"'
  CreateShortcut "$SMPROGRAMS\CPU Memory Cleanup\Browser workbench.lnk" "$INSTDIR\memory-cleanup.exe" '--output-dir "$LOCALAPPDATA\DreamSoul\CPU Memory Cleanup\runs"'
!else
  CreateShortcut "$SMPROGRAMS\CPU Memory Cleanup\CPU Memory Cleanup.lnk" "$INSTDIR\memory-cleanup.exe" '--output-dir "$LOCALAPPDATA\DreamSoul\CPU Memory Cleanup\runs"'
!endif
  CreateShortcut "$SMPROGRAMS\CPU Memory Cleanup\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "DisplayName" "CPU Memory Cleanup"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "DisplayVersion" "${AppVersion}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "Publisher" "DreamSoul-AI"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup" "NoRepair" 1
SectionEnd
Section /o "Desktop shortcut"
!ifdef WindowBuildDir
  CreateShortcut "$DESKTOP\CPU Memory Cleanup.lnk" "$INSTDIR\memory-cleanup-window.exe" '--output-dir "$LOCALAPPDATA\DreamSoul\CPU Memory Cleanup\runs"'
!else
  CreateShortcut "$DESKTOP\CPU Memory Cleanup.lnk" "$INSTDIR\memory-cleanup.exe" '--output-dir "$LOCALAPPDATA\DreamSoul\CPU Memory Cleanup\runs"'
!endif
SectionEnd
Section "Uninstall"
  SetShellVarContext current
  IfFileExists "$INSTDIR\memory-cleanup-window.exe" 0 +2
    Delete "$INSTDIR\memory-cleanup-window.exe"
  IfFileExists "$INSTDIR\WebView2Loader.dll" 0 +2
    Delete "$INSTDIR\WebView2Loader.dll"
  Delete "$INSTDIR\memory-cleanup.exe"
  Delete "$INSTDIR\cpu-memory-cleanup.exe"
  IfErrors 0 +3
    MessageBox MB_ICONSTOP "Close the running service before uninstalling."
    Abort
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\CPU Memory Cleanup\CPU Memory Cleanup.lnk"
  Delete "$SMPROGRAMS\CPU Memory Cleanup\Browser workbench.lnk"
  Delete "$SMPROGRAMS\CPU Memory Cleanup\Uninstall.lnk"
  RMDir "$SMPROGRAMS\CPU Memory Cleanup"
  Delete "$DESKTOP\CPU Memory Cleanup.lnk"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\DreamSoulCpuMemoryCleanup"
  ; User records are outside INSTDIR and intentionally retained.
SectionEnd
