//! Windows Sandbox Adapter — Isolierte, fluechtige Pruefumgebung fuer heruntergeladene Artefakte.
//!
//! Grundsatz „Sicherheit braucht Vertrauen“:
//! Jedes heruntergeladene Artefakt (Mods, Software, Installer) wird nach der statischen
//! Pruefung verbindlich in einer echten, fluechtigen Windows Sandbox bereitgestellt.
//!
//! Sicherheitsgrenzen der Sandbox:
//! - Quarantäne-Artefakt wird ausschliesslich **schreibgeschuetzt** gemountet (`ReadOnly: true`).
//! - Netzwerk: `Disable` (vollstaendig offline, kein Nachladen von Payloads).
//! - Zwischenablage: `Disable` (kein Exfiltrieren oder Einschleusen ueber Clipboard).
//! - vGPU: `Disable` (reines Software-Rendering, keine GPU-Ausbruchsvektoren).
//! - Protected Client: `Enable` (verstaerkte RDP-/App-Container-Isolation).
//! - Audio-/Video-Eingabe: `Disable` (kein Zugriff auf Mikrofon/Kamera).
//! - Drucker: `Disable`.
//! - Kein automatischer Start: Die Sandbox oeffnet ausschliesslich den Explorer im
//!   gemounteten Quarantäneverzeichnis. Der Mensch kann das Artefakt dort isoliert untersuchen.
//! - Nach dem Schliessen der Sandbox werden saemtliche Aenderungen, Dateien und Registry-Eintraege
//!   vom Betriebssystem rueckstandslos verworfen.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SandboxZustand {
    Unavailable,
    Starting,
    Running,
    Closed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SandboxBericht {
    pub zustand: SandboxZustand,
    pub verfuegbar: bool,
    pub isoliert: bool,
    pub hinweis: String,
    pub diagnose: Option<String>,
}

/// Ermittelt den Pfad zur WindowsSandbox.exe.
pub fn windows_sandbox_exe() -> Option<PathBuf> {
    let system_root = std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    let exe = system_root.join(r"System32\WindowsSandbox.exe");
    if exe.is_file() {
        Some(exe)
    } else {
        None
    }
}

/// Prueft, ob CPU-Virtualisierung im BIOS/UEFI aktiviert ist (PF_VIRT_FIRMWARE_ENABLED = 21).
#[cfg(windows)]
pub fn cpu_virtualisierung_aktiv() -> bool {
    extern "system" {
        fn IsProcessorFeaturePresent(ProcessorFeature: u32) -> i32;
    }
    const PF_VIRT_FIRMWARE_ENABLED: u32 = 21;
    unsafe { IsProcessorFeaturePresent(PF_VIRT_FIRMWARE_ENABLED) != 0 }
}

#[cfg(not(windows))]
pub fn cpu_virtualisierung_aktiv() -> bool {
    false
}

/// Prueft, ob Windows Sandbox auf diesem System verfuegbar und einsatzbereit ist:
/// 1. WindowsSandbox.exe existiert in System32 (Windows Pro/Enterprise mit aktiviertem Feature).
/// 2. CPU-Virtualisierung (VT-x / AMD-V) ist im BIOS/UEFI aktiv.
pub fn ist_verfuegbar() -> bool {
    windows_sandbox_exe().is_some() && cpu_virtualisierung_aktiv()
}

/// Erzeugt den Inhalt einer isolierten .wsb-Konfigurationsdatei.
pub fn wsb_konfiguration_bauen(host_quarantaene_ordner: &Path) -> Result<String, String> {
    let kanonisch = dunce::canonicalize(host_quarantaene_ordner)
        .map_err(|e| format!("Quarantäne-Pfad nicht lesbar: {e}"))?;
    let host_pfad_str = kanonisch
        .to_string_lossy()
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;");

    Ok(format!(
        "<Configuration>\r\n\
  <VGpu>Disable</VGpu>\r\n\
  <Networking>Disable</Networking>\r\n\
  <MappedFolders>\r\n\
    <MappedFolder>\r\n\
      <HostFolder>{}</HostFolder>\r\n\
      <SandboxFolder>C:\\Users\\WDAGUtilityAccount\\Desktop\\Quarantine</SandboxFolder>\r\n\
      <ReadOnly>true</ReadOnly>\r\n\
    </MappedFolder>\r\n\
  </MappedFolders>\r\n\
  <LogonCommand>\r\n\
    <Command>explorer.exe C:\\Users\\WDAGUtilityAccount\\Desktop\\Quarantine</Command>\r\n\
  </LogonCommand>\r\n\
  <AudioInput>Disable</AudioInput>\r\n\
  <VideoInput>Disable</VideoInput>\r\n\
  <ProtectedClient>Enable</ProtectedClient>\r\n\
  <PrinterRedirection>Disable</PrinterRedirection>\r\n\
  <ClipboardRedirection>Disable</ClipboardRedirection>\r\n\
  <MemoryInMB>4096</MemoryInMB>\r\n\
</Configuration>",
        host_pfad_str
    ))
}

/// Erzeugt eine temporaere .wsb-Datei fuer ein bestimmtes Artefakt.
pub fn wsb_datei_anlegen(
    quarantaene_ordner: &Path,
    artifact_id: &str,
) -> Result<PathBuf, String> {
    let inhalt = wsb_konfiguration_bauen(quarantaene_ordner)?;
    let temp_dir = std::env::temp_dir();
    let dateiname = format!("mss-sandbox-{artifact_id}.wsb");
    let wsb_pfad = temp_dir.join(dateiname);

    fs::write(&wsb_pfad, inhalt.as_bytes())
        .map_err(|e| format!("Konnte Sandbox-Konfigurationsdatei nicht schreiben: {e}"))?;

    Ok(wsb_pfad)
}

/// Startet eine isolierte Windows Sandbox fuer den uebergebenen Quarantäneordner.
pub fn starten(
    quarantaene_ordner: &Path,
    artifact_id: &str,
) -> Result<SandboxBericht, String> {
    let exe = match windows_sandbox_exe() {
        Some(p) => p,
        None => {
            return Ok(SandboxBericht {
                zustand: SandboxZustand::Unavailable,
                verfuegbar: false,
                isoliert: false,
                hinweis: "Windows Sandbox ist auf diesem Rechner nicht installiert oder im BIOS/UEFI nicht aktiviert. Eine isolierte Sandbox-Prüfung ist hier nicht möglich.".into(),
                diagnose: Some("WindowsSandbox.exe in System32 nicht gefunden".into()),
            });
        }
    };

    let wsb_pfad = wsb_datei_anlegen(quarantaene_ordner, artifact_id)?;
    let wsb_pfad_fuer_cleanup = wsb_pfad.clone();

    let mut cmd = Command::new(&exe);
    cmd.arg(&wsb_pfad);

    #[cfg(windows)]
    {
        // CREATE_NO_WINDOW fuer den Launcher-Befehl
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000);
    }

    let mut child = cmd.spawn().map_err(|e| {
        let _ = fs::remove_file(&wsb_pfad);
        format!("Windows Sandbox konnte nicht gestartet werden: {e}")
    })?;

    // Hintergrundthread ueberwacht den Sandbox-Prozess und raeumt die .wsb-Konfiguration auf
    let laeuft = Arc::new(AtomicBool::new(true));
    let laeuft_clone = Arc::clone(&laeuft);

    std::thread::spawn(move || {
        let _ = child.wait();
        laeuft_clone.store(false, Ordering::SeqCst);
        // Frist von 5 Sekunden, damit WindowsSandbox.exe die Datei fertig gelesen hat
        std::thread::sleep(Duration::from_secs(5));
        let _ = fs::remove_file(&wsb_pfad_fuer_cleanup);
    });

    Ok(SandboxBericht {
        zustand: SandboxZustand::Running,
        verfuegbar: true,
        isoliert: true,
        hinweis: "Flüchtige Windows Sandbox wurde mit schreibgeschütztem Quarantäne-Mount, ohne Netzwerk und ohne Zwischenablage gestartet.".into(),
        diagnose: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn wsb_konfiguration_setzt_alle_sicherheitsbeschraenkungen() {
        let temp = std::env::temp_dir().join(format!("mss-test-sandbox-cfg-{}", std::process::id()));
        let _ = fs::create_dir_all(&temp);

        let xml = wsb_konfiguration_bauen(&temp).unwrap();

        assert!(xml.contains("<Networking>Disable</Networking>"), "Netzwerk muss aus sein");
        assert!(xml.contains("<ClipboardRedirection>Disable</ClipboardRedirection>"), "Clipboard muss aus sein");
        assert!(xml.contains("<VGpu>Disable</VGpu>"), "vGPU muss aus sein");
        assert!(xml.contains("<ProtectedClient>Enable</ProtectedClient>"), "Protected Client muss an sein");
        assert!(xml.contains("<AudioInput>Disable</AudioInput>"), "Audio muss aus sein");
        assert!(xml.contains("<VideoInput>Disable</VideoInput>"), "Video muss aus sein");
        assert!(xml.contains("<PrinterRedirection>Disable</PrinterRedirection>"), "Drucker muss aus sein");
        assert!(xml.contains("<ReadOnly>true</ReadOnly>"), "Host-Folder muss ReadOnly sein");
        assert!(xml.contains("explorer.exe C:\\Users\\WDAGUtilityAccount\\Desktop\\Quarantine"), "Kein Autostart des Artefakts");

        let _ = fs::remove_dir_all(&temp);
    }

    #[test]
    fn wsb_datei_anlegen_und_loeschen() {
        let temp = std::env::temp_dir().join(format!("mss-test-wsb-file-{}", std::process::id()));
        let _ = fs::create_dir_all(&temp);

        let wsb_pfad = wsb_datei_anlegen(&temp, "test-artifact-123").unwrap();
        assert!(wsb_pfad.exists());
        assert!(wsb_pfad.to_string_lossy().ends_with(".wsb"));

        let inhalt = fs::read_to_string(&wsb_pfad).unwrap();
        assert!(inhalt.contains("<Configuration>"));

        let _ = fs::remove_file(&wsb_pfad);
        let _ = fs::remove_dir_all(&temp);
    }
}
