//! Kopien der Browser-Datenbanken für die Dauer eines Imports.
//!
//! Der Quell-Browser darf geöffnet bleiben: gelesen wird nur die Kopie im
//! eigenen Datenordner, nie die Datei des anderen Programms. Die Kopie liegt
//! unter `app_local_data_dir()/browserimport/` und verschwindet mit dem
//! `Drop` des `Schatten`; Reste eines Absturzes räumt `raeume_auf` beim Start
//! weg (ohne einer Verknüpfung zu folgen).

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rusqlite::{Connection, OpenFlags};
use tauri::{AppHandle, Manager};

static ZAEHLER: AtomicU64 = AtomicU64::new(0);

pub(crate) struct Schatten {
    ordner: PathBuf,
}

impl Schatten {
    pub(crate) fn neu(app: &AppHandle) -> Result<Self, &'static str> {
        let basis = app.path().app_local_data_dir().map_err(|_| "schatten")?.join("browserimport");
        Self::neu_in(&basis)
    }

    pub(crate) fn neu_in(basis: &Path) -> Result<Self, &'static str> {
        fs::create_dir_all(basis).map_err(|_| "schatten")?;
        let nonce = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
        let name = format!(
            "{}-{nonce}-{}",
            std::process::id(),
            ZAEHLER.fetch_add(1, Ordering::Relaxed)
        );
        let ordner = basis.join(name);
        fs::create_dir(&ordner).map_err(|_| "schatten")?;
        Ok(Self { ordner })
    }

    pub(crate) fn pfad(&self, name: &str) -> PathBuf {
        self.ordner.join(name)
    }

    /// Kopiert eine Datei samt `-wal`/`-journal`. Fehlt sie, ist das kein
    /// Fehler (`Ok(false)`) — ein Profil darf nur Lesezeichen haben. `wurzel`
    /// ist der Profilordner: eine Verknüpfung oder ein Pfad außerhalb wird
    /// nicht gelesen.
    pub(crate) fn kopiere(&self, quelle: &Path, wurzel: &Path, name: &str, max: u64) -> Result<bool, &'static str> {
        let Some(meta) = quelle_ok(quelle, wurzel) else { return Ok(false) };
        if meta.len() > max {
            return Err("zu_gross");
        }
        let mut letzter = "gesperrt";
        for versuch in 0..3 {
            match self.kopiere_alle(quelle, wurzel, name, max) {
                Ok(()) => return Ok(true),
                Err(code) => letzter = code,
            }
            if versuch < 2 {
                std::thread::sleep(Duration::from_millis(60));
            }
        }
        Err(letzter)
    }

    fn kopiere_alle(&self, quelle: &Path, wurzel: &Path, name: &str, max: u64) -> Result<(), &'static str> {
        let ziel = self.pfad(name);
        let _ = fs::remove_file(&ziel);
        let original = fs::metadata(quelle).map_err(|_| "gesperrt")?;
        fs::copy(quelle, &ziel).map_err(klassifizieren)?;
        if fs::metadata(&ziel).map(|m| m.len()).unwrap_or(0) != original.len() {
            let _ = fs::remove_file(&ziel);
            return Err("gesperrt");
        }
        for endung in ["-wal", "-journal"] {
            let neben = mit_endung(quelle, endung);
            let Some(meta) = quelle_ok(&neben, wurzel) else { continue };
            if meta.len() > max {
                let _ = fs::remove_file(&ziel);
                return Err("zu_gross");
            }
            if let Err(fehler) = fs::copy(&neben, mit_endung(&ziel, endung)) {
                let _ = fs::remove_file(&ziel);
                return Err(klassifizieren(fehler));
            }
        }
        Ok(())
    }
}

impl Drop for Schatten {
    fn drop(&mut self) {
        entfernen(&self.ordner);
    }
}

fn mit_endung(pfad: &Path, endung: &str) -> PathBuf {
    let mut name = pfad.as_os_str().to_os_string();
    name.push(endung);
    PathBuf::from(name)
}

/// Eine Quelldatei muss ein echter Ordner unterhalb der Wurzel sein. Eine
/// Verknüpfung könnte aus dem Profil heraus auf beliebige Dateien zeigen.
fn quelle_ok(quelle: &Path, wurzel: &Path) -> Option<fs::Metadata> {
    let meta = fs::symlink_metadata(quelle).ok()?;
    if !meta.is_file() || ist_verknuepfung(&meta) {
        return None;
    }
    let quelle = quelle.canonicalize().ok()?;
    let wurzel = wurzel.canonicalize().ok()?;
    if !quelle.starts_with(&wurzel) {
        return None;
    }
    Some(meta)
}

fn klassifizieren(fehler: io::Error) -> &'static str {
    // Windows meldet eine geöffnete Datei als Freigabeverletzung (32) oder
    // Zugriff verweigert (5). Alles andere ist ebenfalls kein Grund, die
    // ganze Datei als kaputt zu bezeichnen.
    match fehler.kind() {
        io::ErrorKind::NotFound => "profil_unbekannt",
        _ => "gesperrt",
    }
}

fn ist_verknuepfung(meta: &fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    meta.file_attributes() & 0x400 != 0
}

/// Löscht einen Ordner, ohne einer Verknüpfung zu folgen.
fn entfernen(pfad: &Path) {
    let Ok(meta) = fs::symlink_metadata(pfad) else { return };
    if meta.is_dir() && !ist_verknuepfung(&meta) {
        let _ = fs::remove_dir_all(pfad);
    } else if meta.is_dir() {
        let _ = fs::remove_dir(pfad);
    } else {
        let _ = fs::remove_file(pfad);
    }
}

/// Ein zweites Fenster (`--neues-fenster`) räumt beim Start denselben Ordner;
/// eine laufende Kopie des Hauptfensters darf es nicht unter den Füßen
/// wegziehen. Deshalb bleiben junge Ordner stehen.
const RESTE_ALTER: Duration = Duration::from_secs(60 * 60);

pub(crate) fn raeume_auf(app: &AppHandle) {
    if let Ok(basis) = app.path().app_local_data_dir() {
        raeume_auf_in(&basis.join("browserimport"), RESTE_ALTER);
    }
}

fn raeume_auf_in(basis: &Path, max_alter: Duration) {
    let Ok(meta) = fs::symlink_metadata(basis) else { return };
    if !meta.is_dir() || ist_verknuepfung(&meta) {
        return;
    }
    let Ok(eintraege) = fs::read_dir(basis) else { return };
    let jetzt = SystemTime::now();
    for eintrag in eintraege.flatten() {
        let pfad = eintrag.path();
        let Ok(meta) = fs::symlink_metadata(&pfad) else { continue };
        let alter = meta.modified().ok().and_then(|zeit| jetzt.duration_since(zeit).ok()).unwrap_or_default();
        if alter < max_alter {
            continue;
        }
        entfernen(&pfad);
    }
}

/// Öffnet eine Kopie nur lesend. Lässt SQLite eine WAL-Kopie nicht read-only
/// öffnen, wird sie als eigene Kopie lesend beschrieben geöffnet; `query_only`
/// verhindert jeden Schreibzugriff.
pub(crate) fn oeffnen(pfad: &Path) -> rusqlite::Result<Connection> {
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let conn = Connection::open_with_flags(pfad, flags)
        .or_else(|_| Connection::open_with_flags(pfad, OpenFlags::SQLITE_OPEN_NO_MUTEX))?;
    conn.busy_timeout(Duration::from_secs(5))?;
    conn.execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")?;
    // SQLite öffnet eine Datei erst beim ersten Lesen. Ohne diese Abfrage
    // ginge eine Textdatei als leere Datenbank durch (bis 10/2026).
    conn.query_row("SELECT count(*) FROM sqlite_master", [], |_| Ok(()))?;
    Ok(conn)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::browserimport::testordner;

    #[test]
    fn kopiert_auch_wal_und_raeumt_den_ordner_wieder_weg() {
        let t = testordner("schatten-wal");
        let quelle = t.0.join("History");
        {
            let conn = Connection::open(&quelle).unwrap();
            conn.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE urls (url TEXT); INSERT INTO urls VALUES ('https://a.example/');")
                .unwrap();
            // Die Verbindung bleibt offen: die Daten stehen noch in der WAL.
            let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
            assert!(schatten.kopiere(&quelle, &t.0, "History", 1024 * 1024).unwrap());
            assert!(schatten.pfad("History-wal").is_file());
            assert!(!schatten.pfad("History-shm").exists());
            let kopie = oeffnen(&schatten.pfad("History")).unwrap();
            let anzahl: i64 = kopie.query_row("SELECT COUNT(*) FROM urls", [], |r| r.get(0)).unwrap();
            assert_eq!(anzahl, 1);
            assert!(kopie.execute("INSERT INTO urls VALUES ('https://b.example/')", []).is_err());
            drop(kopie);
            let ordner = schatten.ordner.clone();
            drop(schatten);
            assert!(!ordner.exists());
        }
    }

    #[test]
    fn fehlende_datei_ist_kein_fehler_und_groesse_wird_geprueft() {
        let t = testordner("schatten-grenzen");
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        assert!(!schatten.kopiere(&t.0.join("fehlt"), &t.0, "fehlt", 10).unwrap());
        let quelle = t.0.join("gross");
        fs::write(&quelle, b"0123456789").unwrap();
        assert_eq!(schatten.kopiere(&quelle, &t.0, "gross", 5), Err("zu_gross"));
        assert!(!schatten.pfad("gross").exists());
    }

    #[test]
    fn liest_keine_verknuepfung_aus_dem_profil() {
        use std::os::windows::fs::symlink_file;
        let t = testordner("schatten-symlink");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        let geheim = t.0.join("geheim");
        fs::write(&geheim, b"fremde daten").unwrap();
        let verknuepfung = profil.join("History");
        if symlink_file(&geheim, &verknuepfung).is_err() {
            // Ohne Entwicklermodus darf der Test keine Verknüpfung anlegen.
            return;
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        assert!(!schatten.kopiere(&verknuepfung, &profil, "History", 1024).unwrap());
        assert!(!schatten.pfad("History").exists());
    }

    #[test]
    fn raeumt_reste_beim_start_weg() {
        let t = testordner("schatten-start");
        let basis = t.0.join("browserimport");
        fs::create_dir_all(basis.join("123-456-0")).unwrap();
        fs::write(basis.join("123-456-0").join("History"), b"alt").unwrap();
        fs::write(basis.join("datei"), b"alt").unwrap();

        raeume_auf_in(&basis, Duration::from_secs(3600));
        assert_eq!(fs::read_dir(&basis).unwrap().count(), 2, "junge Kopien bleiben für ein anderes Fenster stehen");

        raeume_auf_in(&basis, Duration::ZERO);
        assert!(fs::read_dir(&basis).unwrap().next().is_none());
    }
}
