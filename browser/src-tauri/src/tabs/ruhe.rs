//! Wann verborgene Tabs schlafen oder ganz weg dürfen.
//!
//! Ein schlafender Tab ist eingefroren (WebView2 `TrySuspend`): kein Skript,
//! keine Zeitgeber, weniger Speicher; beim Zeigen wacht er mit seinem Stand
//! auf. Ein verworfener Tab hat keine Webview mehr, nur noch seine Adresse in
//! der Oberfläche, und lädt beim Zeigen neu.
//!
//! Hier steht nur die Entscheidung, wer dran ist. Was ein Tab gerade tut
//! (Ton, Adresse), prüft der UI-Faden unmittelbar davor (`desktop/schlaf.rs`):
//! ein Tab, der Ton spielt, schläft nie. Verworfen werden nur normale Tabs:
//! private Tabs teilen eine Sitzung im Speicher, die mit dem letzten von ihnen
//! verschwände, samt Anmeldungen.

use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, State};

use super::{Tabs, Zustand};
use crate::schild::sperre::host_normal;

/// So lange muss ein Tab verborgen sein, bevor „Speicher sparen“ ihn verwirft.
pub const VERWERFEN_NACH: Duration = Duration::from_secs(60 * 60);

const AUSNAHMEN_MAX: usize = 200;

#[derive(Debug, Clone, PartialEq)]
pub struct Leistung {
    /// `None`: Tabs schlafen nie.
    pub schlafen_nach: Option<Duration>,
    pub verwerfen: bool,
    /// Hosts, deren Tabs nie schlafen und nie verworfen werden.
    pub ausnahmen: Vec<String>,
}

impl Default for Leistung {
    fn default() -> Self {
        Self { schlafen_nach: Some(Duration::from_secs(30 * 60)), verwerfen: false, ausnahmen: Vec::new() }
    }
}

impl Leistung {
    /// Was die Oberfläche schickt, geprüft: 1 Minute bis 1 Tag, Hosts klein
    /// geschrieben, ohne Schema und Pfad.
    pub fn pruefen(schlafen_minuten: Option<u32>, verwerfen: bool, ausnahmen: Vec<String>) -> Result<Self, String> {
        if schlafen_minuten.is_some_and(|m| !(1..=1440).contains(&m)) {
            return Err("Ungültige Wartezeit".into());
        }
        if ausnahmen.len() > AUSNAHMEN_MAX {
            return Err("Zu viele Ausnahmen".into());
        }
        let hosts = ausnahmen.iter().map(|a| host_normal(a).ok_or("Ungültige Ausnahme")).collect::<Result<Vec<_>, _>>()?;
        Ok(Self { schlafen_nach: schlafen_minuten.map(|m| Duration::from_secs(u64::from(m) * 60)), verwerfen, ausnahmen: hosts })
    }
}

/// Wann Tabs schlafen und ob sie verworfen werden (Einstellungen → Leistung).
#[tauri::command(async)]
pub fn tabs_leistung(tabs: State<'_, Tabs>, schlafen_minuten: Option<u32>, verwerfen: bool, ausnahmen: Vec<String>) -> Result<(), String> {
    let leistung = Leistung::pruefen(schlafen_minuten, verwerfen, ausnahmen)?;
    tabs.0.lock().unwrap().leistung = leistung;
    Ok(())
}

/// Prüft alle halbe Minute, welche verborgenen Tabs schlafen oder weg dürfen.
pub fn starten(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(PRUEFTAKT);
        let tabs = app.state::<Tabs>();
        let (faellig, ausnahmen) = {
            let z = tabs.0.lock().unwrap();
            (z.faellig(Instant::now()), z.leistung.ausnahmen.clone())
        };
        if !faellig.is_empty() {
            #[cfg(windows)]
            super::plattform::ruhen(&app, faellig, ausnahmen);
            #[cfg(not(windows))]
            let _ = (faellig, ausnahmen);
        }
    });
}

const PRUEFTAKT: Duration = Duration::from_secs(30);

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Ruhe {
    Schlafen,
    Verwerfen,
}

impl Zustand {
    /// Welche Tabs jetzt schlafen oder verworfen werden sollen. Der vordere
    /// und der im Vollbild nie; ein nur verdeckter vorderer Tab ist nicht
    /// verborgen.
    pub fn faellig(&self, jetzt: Instant) -> Vec<(String, Ruhe)> {
        let mut liste = Vec::new();
        for (id, tab) in &self.tabs {
            if self.aktiv.as_deref() == Some(id) || self.vollbild.as_deref() == Some(id) {
                continue;
            }
            let Some(seit) = tab.verborgen_seit else { continue };
            let dauer = jetzt.saturating_duration_since(seit);
            if self.leistung.verwerfen && !tab.privat && dauer >= VERWERFEN_NACH {
                liste.push((id.clone(), Ruhe::Verwerfen));
            } else if self.leistung.schlafen_nach.is_some_and(|d| dauer >= d) {
                liste.push((id.clone(), Ruhe::Schlafen));
            }
        }
        liste
    }
}

/// Gehört die Adresse zu einer Ausnahme? `example.com` deckt auch `www.example.com`.
pub fn ausgenommen(url: &str, ausnahmen: &[String]) -> bool {
    let Some(host) = url::Url::parse(url).ok().and_then(|u| u.host_str().map(str::to_ascii_lowercase)) else {
        return false;
    };
    ausnahmen.iter().any(|a| host == *a || host.ends_with(&format!(".{a}")))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tabs::TabDaten;

    fn zustand(tabs: &[(&str, bool, Option<u64>)], jetzt: Instant) -> Zustand {
        let mut z = Zustand::default();
        for (id, privat, minuten) in tabs {
            let verborgen_seit = minuten.map(|m| jetzt - Duration::from_secs(m * 60));
            z.tabs.insert(id.to_string(), TabDaten { privat: *privat, verborgen_seit });
        }
        z
    }

    #[test]
    fn verborgene_tabs_schlafen_nach_der_wartezeit() {
        let jetzt = Instant::now();
        let z = zustand(&[("tab-a", false, Some(31)), ("tab-b", false, Some(29)), ("tab-c", false, None)], jetzt);
        assert_eq!(z.faellig(jetzt), vec![("tab-a".to_string(), Ruhe::Schlafen)]);
    }

    #[test]
    fn der_vordere_und_der_im_vollbild_schlafen_nie() {
        let jetzt = Instant::now();
        let mut z = zustand(&[("tab-a", false, Some(90)), ("tab-b", false, Some(90))], jetzt);
        z.aktiv = Some("tab-a".into());
        z.vollbild = Some("tab-b".into());
        assert!(z.faellig(jetzt).is_empty());
        // Verdeckt (Dialog darüber) ist der vordere Tab trotzdem vorne.
        z.vollbild = None;
        z.verdeckt = true;
        assert_eq!(z.faellig(jetzt), vec![("tab-b".to_string(), Ruhe::Schlafen)]);
    }

    #[test]
    fn verworfen_wird_nur_mit_speicher_sparen_und_nie_privat() {
        let jetzt = Instant::now();
        let mut z = zustand(&[("tab-a", false, Some(61)), ("tab-p", true, Some(61))], jetzt);
        let mut f = z.faellig(jetzt);
        f.sort_by(|a, b| a.0.cmp(&b.0));
        assert_eq!(f, vec![("tab-a".to_string(), Ruhe::Schlafen), ("tab-p".to_string(), Ruhe::Schlafen)]);
        z.leistung.verwerfen = true;
        let mut f = z.faellig(jetzt);
        f.sort_by(|a, b| a.0.cmp(&b.0));
        assert_eq!(f, vec![("tab-a".to_string(), Ruhe::Verwerfen), ("tab-p".to_string(), Ruhe::Schlafen)]);
    }

    #[test]
    fn nie_heisst_nie() {
        let jetzt = Instant::now();
        let mut z = zustand(&[("tab-a", false, Some(120))], jetzt);
        z.leistung.schlafen_nach = None;
        assert!(z.faellig(jetzt).is_empty());
    }

    #[test]
    fn nach_vorne_geholt_zaehlt_die_zeit_neu() {
        let jetzt = Instant::now();
        let mut z = zustand(&[("tab-a", false, Some(90)), ("tab-b", false, None)], jetzt);
        z.aktiv = Some("tab-b".into());
        z.aktiv_setzen(Some("tab-a".into()), jetzt);
        assert_eq!(z.tabs["tab-a"].verborgen_seit, None);
        assert_eq!(z.tabs["tab-b"].verborgen_seit, Some(jetzt));
        assert!(z.faellig(jetzt).is_empty());
    }

    #[test]
    fn ausnahmen_gelten_auch_fuer_unterdomains() {
        let a = vec!["example.com".to_string()];
        assert!(ausgenommen("https://example.com/x", &a));
        assert!(ausgenommen("https://www.example.com/", &a));
        assert!(!ausgenommen("https://notexample.com/", &a));
        assert!(!ausgenommen("about:blank", &a));
    }

    #[test]
    fn die_oberflaeche_schickt_nur_gueltiges() {
        assert!(Leistung::pruefen(Some(0), false, vec![]).is_err());
        assert!(Leistung::pruefen(Some(1441), false, vec![]).is_err());
        assert!(Leistung::pruefen(None, false, vec!["https://x.de/".into()]).is_err());
        let l = Leistung::pruefen(Some(15), true, vec![" *.YouTube.com ".into()]).unwrap();
        assert_eq!(l.schlafen_nach, Some(Duration::from_secs(900)));
        assert_eq!(l.ausnahmen, vec!["youtube.com".to_string()]);
    }
}
