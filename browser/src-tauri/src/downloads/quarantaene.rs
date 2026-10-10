//! Downloads landen zuerst hier: `app_local_data/quarantaene/<zufall>/<name>`.
//! Erst wenn der Virenschutz sie durchgelassen hat, gehen sie an ihr Ziel.
//! Was beim Beenden noch hier lag (abgebrochen, abgestürzt), fällt beim
//! nächsten Start weg; der Ordner gehört nur dem Browser.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use tauri::{AppHandle, Manager};

fn ordner(app: &AppHandle) -> Option<PathBuf> {
    Some(app.path().app_local_data_dir().ok()?.join("quarantaene"))
}

fn zufall() -> u64 {
    static NR: AtomicU64 = AtomicU64::new(0);
    let mut h = RandomState::new().build_hasher();
    h.write_u64(NR.fetch_add(1, Ordering::Relaxed));
    h.finish()
}

/// Freie Bytes auf dem Laufwerk von `pfad`, für den aufrufenden Nutzer.
#[cfg(windows)]
pub fn frei(pfad: &Path) -> Option<u64> {
    use windows::core::HSTRING;
    use windows::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
    let ordner = pfad.parent()?;
    let mut frei = 0u64;
    unsafe { GetDiskFreeSpaceExW(&HSTRING::from(ordner.as_os_str()), Some(&mut frei), None, None) }.ok()?;
    Some(frei)
}

/// Ein neuer, leerer Platz für einen Download mit diesem Namen.
pub fn neuer_platz(app: &AppHandle, name: &str) -> Option<PathBuf> {
    let unterordner = ordner(app)?.join(format!("{:016x}", zufall()));
    std::fs::create_dir_all(&unterordner).ok()?;
    Some(unterordner.join(name))
}

/// Löscht den Unterordner eines Downloads samt Resten (`.crdownload`). Nur
/// einen Ordner, der direkt in `quarantaene` liegt.
pub fn wegraeumen(datei: &Path) {
    let Some(unterordner) = datei.parent() else { return };
    if unterordner.parent().and_then(Path::file_name).is_some_and(|n| n == "quarantaene") {
        let _ = std::fs::remove_dir_all(unterordner);
    }
}

/// Beim Start läuft noch kein Download: alles in der Quarantäne ist Rest.
/// Ein Verweis (Junction, Symlink) an Stelle des Ordners wird nicht verfolgt.
pub fn beim_start(app: &AppHandle) {
    let Some(o) = ordner(app) else { return };
    if std::fs::symlink_metadata(&o).is_ok_and(|m| m.is_dir() && !m.file_type().is_symlink()) {
        if let Err(fehler) = std::fs::remove_dir_all(&o) {
            eprintln!("[MSB] Quarantäne nicht geleert: {fehler}");
        }
    }
}

/// Verschiebt die geprüfte Datei. Liegt das Ziel auf einem anderen Laufwerk,
/// wird kopiert und gelöscht; die Herkunftsmarke (`Zone.Identifier`) kommt in
/// beiden Fällen mit.
pub fn verschieben(von: &Path, nach: &Path) -> io::Result<()> {
    if std::fs::rename(von, nach).is_ok() {
        return Ok(());
    }
    std::fs::copy(von, nach)?;
    std::fs::remove_file(von)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raeumt_nur_unterordner_der_quarantaene_weg() {
        let basis = std::env::temp_dir().join(format!("msb-q-{}", std::process::id()));
        let fremd = basis.join("fremd");
        let platz = basis.join("quarantaene").join("abc");
        std::fs::create_dir_all(&fremd).unwrap();
        std::fs::create_dir_all(&platz).unwrap();
        std::fs::write(fremd.join("a.txt"), b"x").unwrap();
        std::fs::write(platz.join("a.txt.crdownload"), b"x").unwrap();

        wegraeumen(&fremd.join("a.txt"));
        assert!(fremd.join("a.txt").exists(), "nur Ordner in der Quarantäne");
        wegraeumen(&platz.join("a.txt"));
        assert!(!platz.exists());

        std::fs::write(fremd.join("b.txt"), b"inhalt").unwrap();
        verschieben(&fremd.join("b.txt"), &basis.join("b.txt")).unwrap();
        assert_eq!(std::fs::read(basis.join("b.txt")).unwrap(), b"inhalt");
        std::fs::remove_dir_all(&basis).unwrap();
        assert_ne!(zufall(), zufall());
    }
}
