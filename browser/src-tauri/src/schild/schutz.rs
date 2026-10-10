//! Jugend- und Suchtschutz: welche Kategorien gesperrt sind und wie schwer es
//! ist, das zu lockern. Hier steht nur das Modell; Datei, Befehle und Uhr
//! liegen in `schutz_dienst.rs`.
//!
//! - Verschärfen gilt sofort.
//! - Solange seit dem Einschalten nicht gebunden wurde, gilt auch Lockern
//!   sofort: ohne Bindung hat sich niemand auf etwas festgelegt.
//! - Beim Einschalten bindet man sich (1 bis 90 Tage): in der Zeit lässt sich
//!   nichts lockern, nicht einmal beantragen. Verlängern geht jederzeit.
//! - Wer nach einer Bindung lockert (Schutz aus, Kategorie oder eigene Sperre weg,
//!   Ausnahme dazu, kürzere Wartezeit), stellt einen Antrag. Er wird nach der
//!   Wartezeit (24 h bis 7 Tage) für eine Stunde bestätigbar und verfällt
//!   danach. Wer abbricht oder verfallen lässt, kann einen Tag lang keinen
//!   neuen stellen.
//! - Beantragen und Bestätigen messen an der Netzzeit (`netzzeit.rs`); ohne
//!   Netz geht beides nicht.
//!
//! Das hält den Impuls auf und auch die Vorbereitung darauf, keinen
//! entschlossenen Menschen: wer die Datei löscht oder einen anderen Browser
//! nimmt, kommt vorbei. So steht es auch in der Oberfläche.

use serde::{Deserialize, Serialize};

use super::kategorien::Kategorie;
use super::sperre::host_normal;

const HOSTS_MAX: usize = 500;
const STUNDE: u64 = 3600;
const TAG: u64 = 24 * STUNDE;
/// Wartezeiten bis eine Lockerung bestätigt werden kann, in Stunden.
pub const WARTEZEITEN: [u32; 3] = [24, 72, 168];
/// Wie lange man sich beim Einschalten bindet, in Tagen.
pub const BINDUNGEN: [u32; 4] = [1, 7, 30, 90];
/// So lange lässt sich eine fällige Lockerung bestätigen.
pub const FENSTER: u64 = STUNDE;
/// Nach Abbruch oder Verfall: so lange kein neuer Antrag.
pub const ABKUEHLEN: u64 = TAG;
/// Seltener schreibt ein Sperrtreffer die Serie nicht fort.
const SERIE_TAKT: u64 = 60;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Regeln {
    pub aktiv: bool,
    pub kategorien: Vec<Kategorie>,
    /// Hosts, die zusätzlich gesperrt sind.
    pub eigene: Vec<String>,
    /// Hosts, die nie gesperrt sind.
    pub ausnahmen: Vec<String>,
    /// Bis eine Lockerung bestätigt werden kann. Dateien von vor dem
    /// 08.10.2026 tragen statt dessen `huerde`; serde übergeht das Feld, und
    /// es gilt die kürzeste Wartezeit, länger als jede alte Hürde.
    pub wartezeit_stunden: u32,
}

impl Default for Regeln {
    fn default() -> Self {
        Self { aktiv: false, kategorien: Vec::new(), eigene: Vec::new(), ausnahmen: Vec::new(), wartezeit_stunden: WARTEZEITEN[0] }
    }
}

fn hosts(liste: &[String]) -> Result<Vec<String>, String> {
    if liste.len() > HOSTS_MAX {
        return Err("Zu viele Einträge".into());
    }
    let mut hosts = liste.iter().map(|h| host_normal(h).ok_or("Ungültige Domain")).collect::<Result<Vec<_>, _>>()?;
    hosts.sort();
    hosts.dedup();
    Ok(hosts)
}

impl Regeln {
    /// Was die Oberfläche schickt, geprüft und geordnet.
    pub fn pruefen(mut self) -> Result<Self, String> {
        self.kategorien.sort();
        self.kategorien.dedup();
        self.eigene = hosts(&self.eigene)?;
        self.ausnahmen = hosts(&self.ausnahmen)?;
        if !WARTEZEITEN.contains(&self.wartezeit_stunden) {
            return Err("Ungültige Wartezeit".into());
        }
        Ok(self)
    }

    /// Alles gesperrt; gilt, wenn die Datei nicht zu lesen ist.
    pub fn streng() -> Self {
        Self { aktiv: true, kategorien: Kategorie::ALLE.to_vec(), ..Self::default() }
    }

    pub fn sichere_suche(&self) -> bool {
        self.aktiv && self.kategorien.contains(&Kategorie::Erwachsene)
    }
}

/// Nimmt `neu` dem Nutzer irgendetwas von dem Schutz, den `alt` gibt?
pub fn lockert(alt: &Regeln, neu: &Regeln) -> bool {
    if !alt.aktiv {
        return false;
    }
    !neu.aktiv
        || alt.kategorien.iter().any(|k| !neu.kategorien.contains(k))
        || alt.eigene.iter().any(|h| !neu.eigene.contains(h))
        || neu.ausnahmen.iter().any(|h| !alt.ausnahmen.contains(h))
        || neu.wartezeit_stunden < alt.wartezeit_stunden
}

/// Der Teil von `neu`, der sofort gelten darf: alles Strengere, nichts Lockeres.
pub fn strenger(alt: &Regeln, neu: &Regeln) -> Regeln {
    if !alt.aktiv {
        return neu.clone();
    }
    let mut r = alt.clone();
    r.kategorien.extend(neu.kategorien.iter().filter(|k| !alt.kategorien.contains(k)));
    r.kategorien.sort();
    r.eigene.extend(neu.eigene.iter().filter(|h| !alt.eigene.contains(h)).cloned());
    r.eigene.sort();
    r.ausnahmen.retain(|h| neu.ausnahmen.contains(h));
    r.wartezeit_stunden = alt.wartezeit_stunden.max(neu.wartezeit_stunden);
    r
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Antrag {
    pub ziel: Regeln,
    /// Netzzeit in Unix-Sekunden, ab der sich der Antrag bestätigen lässt;
    /// eine Stunde lang ([`FENSTER`]).
    pub faellig: u64,
}

/// Tage ohne Sperrtreffer. Bleibt auf dem Gerät, geht an keinen Dienst.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Serie {
    /// Seit wann (Unix-Sekunden) keine Seite gesperrt werden musste; 0 = nie.
    pub seit: u64,
    pub rekord_tage: u64,
}

impl Serie {
    pub fn tage(&self, jetzt: u64) -> u64 {
        if self.seit == 0 {
            0
        } else {
            jetzt.saturating_sub(self.seit) / TAG
        }
    }

    pub fn rekord(&self, jetzt: u64) -> u64 {
        self.rekord_tage.max(self.tage(jetzt))
    }

    /// Eine Seite wurde gesperrt. `true`, wenn sich gespeicherte Werte änderten.
    pub fn treffer(&mut self, jetzt: u64) -> bool {
        if self.seit != 0 && jetzt.saturating_sub(self.seit) < SERIE_TAKT {
            return false;
        }
        self.rekord_tage = self.rekord(jetzt);
        self.seit = jetzt;
        true
    }
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Schutz {
    pub regeln: Regeln,
    pub antrag: Option<Antrag>,
    /// Bis hierhin (Unix-Sekunden) lässt sich nichts lockern.
    pub gebunden_bis: u64,
    /// Vorher kein neuer Antrag (nach Abbruch oder Verfall).
    pub naechster_antrag_ab: u64,
    /// Seit dem Einschalten nie gebunden: Lockern gilt sofort. Eine kaputte
    /// Datei gilt als gebunden gewesen; alte Dateien ohne das Feld liest
    /// `schutz_dienst::lesen` an `gebunden_bis`.
    pub ungebunden: bool,
    pub serie: Serie,
}

impl Schutz {
    /// Übernimmt den Wunsch `neu`: Strengeres sofort, Lockeres als Antrag,
    /// ohne vorige Bindung ebenfalls sofort.
    /// Bleibt ein Antrag offen, der nicht lockerer wird, behält er seine
    /// Fälligkeit; ein lockererer beginnt von vorn und braucht die Netzzeit
    /// `netz`. Fehler als Code für die Oberfläche: `ohne_netz`, `gebunden`,
    /// `abkuehlen`; dann bleibt alles, wie es war.
    pub fn aendern(&mut self, neu: Regeln, jetzt: u64, netz: Option<u64>) -> Result<(), String> {
        if !lockert(&self.regeln, &neu) {
            if neu.aktiv && !self.regeln.aktiv {
                self.serie.seit = jetzt;
                self.ungebunden = true;
            }
            self.regeln = neu;
            self.antrag = None;
            return Ok(());
        }
        if self.ungebunden {
            self.regeln = neu;
            self.antrag = None;
            return Ok(());
        }
        let sofort = strenger(&self.regeln, &neu);
        if let Some(a) = self.antrag.as_mut().filter(|a| !lockert(&a.ziel, &neu)) {
            a.ziel = neu;
            self.regeln = sofort;
            return Ok(());
        }
        let zeit = netz.ok_or("ohne_netz")?;
        if zeit < self.gebunden_bis {
            return Err("gebunden".into());
        }
        if zeit < self.naechster_antrag_ab {
            return Err("abkuehlen".into());
        }
        self.antrag = Some(Antrag { faellig: zeit + u64::from(sofort.wartezeit_stunden) * STUNDE, ziel: neu });
        self.regeln = sofort;
        Ok(())
    }

    /// Bindet für `tage` ab `zeit`, oder verlängert. Ein offener Antrag fällt.
    pub fn binden(&mut self, tage: u32, zeit: u64) -> Result<(), String> {
        if !BINDUNGEN.contains(&tage) {
            return Err("Ungültige Dauer".into());
        }
        if !self.regeln.aktiv {
            return Err("Der Schutz ist aus".into());
        }
        self.gebunden_bis = self.gebunden_bis.max(zeit + u64::from(tage) * TAG);
        self.ungebunden = false;
        self.antrag = None;
        Ok(())
    }

    /// Wendet einen fälligen Antrag an, gemessen an der Netzzeit. Fehler:
    /// `kein_antrag`, `ohne_netz`, `zu_frueh`, `verfallen`. Ein verfallener
    /// Antrag ist danach weg.
    pub fn bestaetigen(&mut self, netz: Option<u64>) -> Result<(), String> {
        let Some(faellig) = self.antrag.as_ref().map(|a| a.faellig) else {
            return Err("kein_antrag".into());
        };
        let zeit = netz.ok_or("ohne_netz")?;
        if zeit < faellig {
            return Err("zu_frueh".into());
        }
        if zeit >= faellig + FENSTER {
            self.abbrechen(zeit);
            return Err("verfallen".into());
        }
        self.regeln = self.antrag.take().unwrap().ziel;
        Ok(())
    }

    /// Nimmt einen offenen Antrag zurück; danach einen Tag kein neuer.
    pub fn abbrechen(&mut self, zeit: u64) {
        if self.antrag.take().is_some() {
            self.naechster_antrag_ab = self.naechster_antrag_ab.max(zeit + ABKUEHLEN);
        }
    }

    /// Lässt einen Antrag verfallen, dessen Fenster vorbei ist. Mit der Uhr
    /// des Rechners: geht sie vor, verfällt er früher, und das ist strenger.
    pub fn ablaufen(&mut self, jetzt: u64) -> bool {
        match &self.antrag {
            Some(a) if jetzt >= a.faellig + FENSTER => {
                self.abbrechen(jetzt);
                true
            }
            _ => false,
        }
    }
}

#[cfg(test)]
#[path = "schutz_tests.rs"]
mod tests;
