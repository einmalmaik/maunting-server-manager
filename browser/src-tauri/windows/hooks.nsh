; Meldet den Browser bei Windows als Browser an, damit er unter
; „Standard-Apps“ zur Wahl steht. Festlegen kann ihn nur der Nutzer selbst.
; Nur http und https: Dateien (.html) öffnet der Browser nicht.
; Die Werte liest `src/widget/ohne/windows_standard.rs`.
; SHCTX ist bei der Installation für den eigenen Nutzer HKCU.

!define MSB_NAME "Maunting Secure Browser"
!define MSB_CLIENT "Software\Clients\StartMenuInternet\MauntingSecureBrowser"
!define MSB_PROGID "MSBURL"

!macro NSIS_HOOK_POSTINSTALL
  WriteRegStr SHCTX "${MSB_CLIENT}" "" "${MSB_NAME}"
  WriteRegStr SHCTX "${MSB_CLIENT}\DefaultIcon" "" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr SHCTX "${MSB_CLIENT}\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe"'
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities" "ApplicationName" "${MSB_NAME}"
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities" "ApplicationIcon" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities" "ApplicationDescription" "Browser von Maunting Studios ohne Werbung und Tracker"
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities\StartMenu" "StartMenuInternet" "MauntingSecureBrowser"
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities\URLAssociations" "http" "${MSB_PROGID}"
  WriteRegStr SHCTX "${MSB_CLIENT}\Capabilities\URLAssociations" "https" "${MSB_PROGID}"
  WriteRegStr SHCTX "Software\RegisteredApplications" "${MSB_NAME}" "${MSB_CLIENT}\Capabilities"
  WriteRegStr SHCTX "Software\Classes\${MSB_PROGID}" "" "${MSB_NAME} URL"
  WriteRegStr SHCTX "Software\Classes\${MSB_PROGID}" "FriendlyTypeName" "${MSB_NAME} URL"
  WriteRegStr SHCTX "Software\Classes\${MSB_PROGID}\DefaultIcon" "" "$INSTDIR\${MAINBINARYNAME}.exe,0"
  WriteRegStr SHCTX "Software\Classes\${MSB_PROGID}\shell\open\command" "" '"$INSTDIR\${MAINBINARYNAME}.exe" "%1"'
  ; SHCNE_ASSOCCHANGED: Windows liest die Zuordnungen neu.
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegKey SHCTX "${MSB_CLIENT}"
  DeleteRegValue SHCTX "Software\RegisteredApplications" "${MSB_NAME}"
  DeleteRegKey SHCTX "Software\Classes\${MSB_PROGID}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, p 0, p 0)'
!macroend
