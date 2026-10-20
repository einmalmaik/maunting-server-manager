//! APK-Integritätsprüfung (ZIP-Magic & EOCD-Signatur).
//!
//! Prüft, ob eine Datei ein vollständiges, unbeschädigtes APK (ZIP-Archiv) ist:
//! - Datei existiert und ist mindestens 10 MB groß.
//! - Beginnt mit der ZIP-Header-Signatur `PK\x03\x04` ([0x50, 0x4B, 0x03, 0x04]).
//! - Endet mit dem End-of-Central-Directory Record `PK\x05\x06` ([0x50, 0x4B, 0x05, 0x06])
//!   innerhalb der letzten 65.557 Bytes.

use std::path::Path;

pub fn ist_valides_apk(pfad: &Path) -> bool {
    use std::io::{Read, Seek, SeekFrom};
    let Ok(mut file) = std::fs::File::open(pfad) else {
        return false;
    };
    let Ok(meta) = file.metadata() else {
        return false;
    };
    let len = meta.len();
    if len < 10_000_000 {
        return false;
    }

    let mut header = [0u8; 4];
    if file.read_exact(&mut header).is_err() || header != [0x50, 0x4B, 0x03, 0x04] {
        return false;
    }

    let tail_len = (len.min(65557)) as usize;
    if file.seek(SeekFrom::End(-(tail_len as i64))).is_err() {
        return false;
    }

    let mut tail_buffer = vec![0u8; tail_len];
    if file.read_exact(&mut tail_buffer).is_err() {
        return false;
    }

    tail_buffer.windows(4).any(|w| w == [0x50, 0x4B, 0x05, 0x06])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_apk_validierung_datei_nicht_existent() {
        let fake_path = Path::new("ungueltiger_pfad_nicht_vorhanden.apk");
        assert!(!ist_valides_apk(fake_path));
    }

    #[test]
    fn test_apk_validierung_datei_zu_klein() {
        let temp_dir = std::env::temp_dir();
        let datei_pfad = temp_dir.join("test_klein.apk");
        {
            let mut f = std::fs::File::create(&datei_pfad).unwrap();
            f.write_all(b"PK\x03\x04nur ein kleiner test").unwrap();
        }
        let ergebnis = ist_valides_apk(&datei_pfad);
        let _ = std::fs::remove_file(&datei_pfad);
        assert!(!ergebnis, "Dateien unter 10 MB dürfen nicht als gültiges APK akzeptiert werden");
    }

    #[test]
    fn test_apk_validierung_falsche_magic_bytes() {
        let temp_dir = std::env::temp_dir();
        let datei_pfad = temp_dir.join("test_falsche_magic.apk");
        {
            let mut f = std::fs::File::create(&datei_pfad).unwrap();
            f.set_len(10_000_001).unwrap();
            // Startet nicht mit PK\x03\x04
            f.write_all(b"NOPE").unwrap();
        }
        let ergebnis = ist_valides_apk(&datei_pfad);
        let _ = std::fs::remove_file(&datei_pfad);
        assert!(!ergebnis, "Dateien ohne ZIP-Magic PK\\x03\\x04 müssen abgelehnt werden");
    }

    #[test]
    fn test_apk_validierung_erfolgreich() {
        use std::io::Seek;
        let temp_dir = std::env::temp_dir();
        let datei_pfad = temp_dir.join("test_gueltig.apk");
        {
            let mut f = std::fs::File::create(&datei_pfad).unwrap();
            f.set_len(10_000_100).unwrap();
            // Header PK\x03\x04
            f.seek(std::io::SeekFrom::Start(0)).unwrap();
            f.write_all(&[0x50, 0x4B, 0x03, 0x04]).unwrap();
            // EOCD PK\x05\x06 in den letzten Bytes
            f.seek(std::io::SeekFrom::End(-22)).unwrap();
            f.write_all(&[0x50, 0x4B, 0x05, 0x06]).unwrap();
        }
        let ergebnis = ist_valides_apk(&datei_pfad);
        let _ = std::fs::remove_file(&datei_pfad);
        assert!(ergebnis, "Vollständiges ZIP/APK mit Magic-Header und EOCD muss gültig sein");
    }
}
