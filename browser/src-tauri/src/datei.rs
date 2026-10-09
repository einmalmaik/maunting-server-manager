//! Dateien, die ein Absturz nicht halb hinterlassen darf: Konfiguration,
//! Jugendschutz und geladene Listen.

use std::path::Path;
use std::{fs, io};

/// Schreibt erst daneben (`<name>.part`) und benennt dann um. Ein Absturz
/// mitten im Schreiben lässt die alte Datei stehen.
pub fn ersetzen(ziel: &Path, inhalt: impl AsRef<[u8]>) -> io::Result<()> {
    let mut teil = ziel.as_os_str().to_owned();
    teil.push(".part");
    fs::write(&teil, inhalt)?;
    fs::rename(&teil, ziel)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ersetzt_ganz_und_laesst_nichts_daneben() {
        let ordner = std::env::temp_dir().join(format!("msb-datei-{}", std::process::id()));
        fs::create_dir_all(&ordner).unwrap();
        let ziel = ordner.join("konfig.json");
        ersetzen(&ziel, "alt").unwrap();
        ersetzen(&ziel, "neu").unwrap();
        assert_eq!(fs::read_to_string(&ziel).unwrap(), "neu");
        assert_eq!(fs::read_dir(&ordner).unwrap().count(), 1);
        fs::remove_dir_all(&ordner).unwrap();
    }
}
