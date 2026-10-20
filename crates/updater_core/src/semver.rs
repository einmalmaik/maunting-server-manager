//! SemVer-Vergleich für Versionsnummern.

/// Prüft, ob eine Version neuer ist als die aktuelle (SemVer-Zahlenvergleich).
/// Ignoriert führende `v`-Präfixe.
pub fn ist_neuer(ziel: &str, aktuell: &str) -> bool {
    let ziel_teile: Vec<u64> = ziel
        .trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse().ok())
        .collect();
    let akt_teile: Vec<u64> = aktuell
        .trim_start_matches('v')
        .split('.')
        .filter_map(|s| s.parse().ok())
        .collect();

    for (z, a) in ziel_teile.iter().zip(akt_teile.iter()) {
        if z > a {
            return true;
        }
        if z < a {
            return false;
        }
    }
    ziel_teile.len() > akt_teile.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_vergleich_erkennt_neuere_versionen() {
        assert!(ist_neuer("0.2.0", "0.1.9"));
        assert!(ist_neuer("1.0.0", "0.9.9"));
        assert!(ist_neuer("v0.1.10", "0.1.9"));
        assert!(ist_neuer("0.1.9.1", "0.1.9"));
        assert!(!ist_neuer("0.1.9", "0.1.9"));
        assert!(!ist_neuer("0.1.8", "0.1.9"));
        assert!(!ist_neuer("0.1.0", "1.0.0"));
    }
}
