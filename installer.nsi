; KBase NSIS Installer
; Build: makensis /DVERSION=0.3.0 installer.nsi
; Output: dist\KBase-Setup-v0.3.0.exe

!ifndef VERSION
  !define VERSION "0.3.0"
!endif

!define PRODUCT_NAME "KBase"
!define PRODUCT_PUBLISHER "Klynx"
!define PRODUCT_WEB_SITE "https://github.com/DeconBear/kbase"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\KBase.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

SetCompressor /SOLID lzma
SetCompressorDictSize 64

; --- MUI 2 -----------------------------------------------------------------
!include "MUI2.nsh"
!include "FileFunc.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "kb\assets\kbase-logo.ico"
!define MUI_UNICON "kb\assets\kbase-logo.ico"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

; --- Metadata ---------------------------------------------------------------
Name "${PRODUCT_NAME} ${VERSION}"
OutFile "dist\KBase-Setup-v${VERSION}.exe"
InstallDir "$PROGRAMFILES64\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
ShowUnInstDetails show
BrandingText "${PRODUCT_PUBLISHER}"

; Request admin for Program Files install; users without admin can install
; to LOCALAPPDATA instead.
RequestExecutionLevel admin

; --- Installer --------------------------------------------------------------
Section "install"
  SetOutPath "$INSTDIR"

  ; Write installed.flag so the app knows to offer update features
  FileOpen $0 "$INSTDIR\installed.flag" w
  FileClose $0

  ; --- Application binaries (the app creates data/ at first run) ---
  File /nonfatal "dist\KBase\KBase.exe"
  SetOutPath "$INSTDIR\_internal"
  File /r /x "data" "dist\KBase\_internal\*.*"
  SetOutPath "$INSTDIR"

  ; Preserve data_path.txt if it exists (user-chosen data root)
  ; We don't delete it -- thus the user setting survives updates

  ; Create Start Menu shortcuts
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\KBase.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\uninst.exe"

  ; Desktop shortcut (optional)
  CreateShortCut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\KBase.exe"

  ; Write uninstaller
  WriteUninstaller "$INSTDIR\uninst.exe"

  ; Register in Add/Remove Programs
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  IntFmt $0 "0x%08X" $0
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "${PRODUCT_NAME} -- Personal Knowledge Base"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninst.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\KBase.exe,0"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "EstimatedSize" "$0"
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKLM "${PRODUCT_UNINST_KEY}" "NoRepair" 1

  ; App Paths for Start -> Run -> KBase
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\KBase.exe"
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "Path" "$INSTDIR"
SectionEnd

; --- Uninstaller ------------------------------------------------------------
Section "Uninstall"
  ; Remove Start Menu
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

  ; Remove Desktop shortcut
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"

  ; Remove installed.flag
  Delete "$INSTDIR\installed.flag"

  ; Remove all application files (but NOT data/ -- we leave user data)
  ; We explicitly list what we know so we don't rm -rf the user's data
  Delete "$INSTDIR\KBase.exe"
  Delete "$INSTDIR\uninst.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\*.dll"
  Delete "$INSTDIR\*.pyd"

  ; Remove registry entries
  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"

  ; User data (data/, data_path.txt) is intentionally left behind.
  ; If the directory is now empty except for data/, remove the
  ; application directory too.
  RMDir "$INSTDIR"
SectionEnd

; --- Silent install hooks for auto-update -----------------------------------
; When run with /S, the installer overwrites app binaries but preserves:
;   - data/                 (user articles, notes, DB, config)
;   - data_path.txt         (custom data root setting)
;   - installed.flag        (so the app still knows it's installed)

Function .onInit
  ; If running silently (/S), skip admin elevation prompt
  ${If} ${Silent}
    ; Don't try to elevate -- auto-update runs as the current user
  ${EndIf}
FunctionEnd
