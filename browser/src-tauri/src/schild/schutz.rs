//! Jugend- und Suchtschutz: welche Kategorien gesperrt sind und wie schwer es
//! ist, das zu lockern.
//!
//! Verschärfen gilt sofort. Wer lockert (Schutz aus, Kategorie oder eigene
//! Sperre weg, Ausnahme dazu, kürzere oder andere Hürde), stellt einen
//! Antrag: Rust merkt ihn sich samt Fälligkeit und wendet ihn erst danach an,
//! beim Countdown von selbst, beim Abtippen erst mit dem richtigen Text. Die
//! Oberfläche zeigt nur den Stand. Die Regeln liegen in einer eigenen Datei,
//! an die `konfig_aendern` nicht herankommt.
//!
//! Das hält den schnellen Impuls auf, keinen entschlossenen Menschen: wer die
//! Datei von Hand ändert, die Uhr verstellt oder einen anderen Browser nimmt,
//! kommt vorbei. So steht es auch in der Oberfläche.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};

use super::kategorien::{self, Kategorie};
use super::sperre::{self, host_normal, Sperre};

const DATEI: &str = "msb_schutz.json";
const EREIGNIS: &str = "msb:schutz";
const HOSTS_MAX: usize = 500;
pub const COUNTDOWNS: [u32; 4] = [5, 15, 60, 24 * 60];
/// Wer abtippt, braucht für diesen Text mindestens so viele Sekunden je acht
/// Zeichen; schneller geht es nur mit Einfügen.
const ZEICHEN_JE_SEKUNDE: u64 = 8;
const WOERTER: usize = 40;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "art", rename_all = "snake_case")]
pub enum Huerde {
    Countdown { minuten: u32 },
    Abtippen,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Regeln {
    pub aktiv: bool,
    pub kategorien: Vec<Kategorie>,
    /// Hosts, die zusätzlich gesperrt sind.
    pub eigene: Vec<String>,
    /// Hosts, die nie gesperrt sind.
    pub ausnahmen: Vec<String>,
    pub huerde: Huerde,
}

impl Default for Regeln {
    fn default() -> Self {
        Self { aktiv: false, kategorien: Vec::new(), eigene: Vec::new(), ausnahmen: Vec::new(), huerde: Huerde::Countdown { minuten: 15 } }
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
        if let Huerde::Countdown { minuten } = self.huerde {
            if !COUNTDOWNS.contains(&minuten) {
                return Err("Ungültige Wartezeit".into());
            }
        }
        Ok(self)
    }

    fn sichere_suche(&self) -> bool {
        self.aktiv && self.kategorien.contains(&Kategorie::Erwachsene)
    }
}

fn huerde_lockert(alt: &Huerde, neu: &Huerde) -> bool {
    match (alt, neu) {
        (Huerde::Countdown { minuten: a }, Huerde::Countdown { minuten: b }) => b < a,
        (Huerde::Abtippen, Huerde::Abtippen) => false,
        _ => true,
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
        || huerde_lockert(&alt.huerde, &neu.huerde)
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
    if !huerde_lockert(&alt.huerde, &neu.huerde) {
        r.huerde = neu.huerde.clone();
    }
    r
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Antrag {
    pub ziel: Regeln,
    /// Unix-Sekunden, ab denen der Antrag gilt.
    pub faellig: u64,
    /// Beim Abtippen der Text; gilt erst, wenn er eingegeben ist.
    pub text: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Schutz {
    pub regeln: Regeln,
    pub antrag: Option<Antrag>,
}

fn zufall(nr: u64) -> u64 {
    let mut h = RandomState::new().build_hasher();
    h.write_u64(nr);
    h.finish()
}

/// Ausgedachte Wörter aus Silben, ohne y und z (deutsche und englische
/// Tastatur vertauschen sie) und ohne Umlaute.
pub fn abtipptext() -> String {
    const KONSONANTEN: &[u8] = b"bdfgklmnprstvw";
    const VOKALE: &[u8] = b"aeiou";
    (0..WOERTER as u64)
        .map(|w| {
            let z = zufall(w);
            (0..2 + z % 2)
                .map(|s| {
                    let z = z >> (s * 8);
                    format!("{}{}", KONSONANTEN[(z % 14) as usize] as char, VOKALE[((z >> 4) % 5) as usize] as char)
                })
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn wartezeit(huerde: &Huerde, text: Option<&str>) -> u64 {
    match huerde {
        Huerde::Countdown { minuten } => u64::from(*minuten) * 60,
        Huerde::Abtippen => text.map_or(0, |t| t.len() as u64 / ZEICHEN_JE_SEKUNDE),
    }
}

fn gleich_getippt(a: &str, b: &str) -> bool {
    a.split_whitespace().eq(b.split_whitespace())
}

impl Schutz {
    /// Übernimmt den Wunsch `neu`: Strengeres sofort, Lockeres als Antrag.
    /// Bleibt ein Antrag offen, der nicht lockerer wird, behält er seine
    /// Fälligkeit; sonst beginnt die Wartezeit von vorn.
    pub fn aendern(&mut self, neu: Regeln, jetzt: u64) {
        let sofort = strenger(&self.regeln, &neu);
        if !lockert(&self.regeln, &neu) {
            self.regeln = neu;
            self.antrag = None;
            return;
        }
        let bleibt = self.antrag.take().filter(|a| !lockert(&a.ziel, &neu));
        self.antrag = Some(match bleibt {
            Some(a) => Antrag { ziel: neu, ..a },
            None => {
                let text = (sofort.huerde == Huerde::Abtippen).then(abtipptext);
                Antrag { faellig: jetzt + wartezeit(&sofort.huerde, text.as_deref()), ziel: neu, text }
            }
        });
        self.regeln = sofort;
    }

    /// Wendet einen abgelaufenen Countdown an. `true`, wenn sich etwas änderte.
    pub fn faellig_anwenden(&mut self, jetzt: u64) -> bool {
        match &self.antrag {
            Some(a) if a.text.is_none() && jetzt >= a.faellig => {
                self.regeln = self.antrag.take().unwrap().ziel;
                true
            }
            _ => false,
        }
    }

    /// Fehler als Code für die Oberfläche: `kein_antrag`, `text_falsch`, `zu_schnell`.
    pub fn bestaetigen(&mut self, eingabe: &str, jetzt: u64) -> Result<(), String> {
        let Some(Antrag { text: Some(text), faellig, .. }) = &self.antrag else {
            return Err("kein_antrag".into());
        };
        if !gleich_getippt(text, eingabe) {
            return Err("text_falsch".into());
        }
        if jetzt < *faellig {
            return Err("zu_schnell".into());
        }
        self.regeln = self.antrag.take().unwrap().ziel;
        Ok(())
    }

    fn sperre(&self, app: &AppHandle) -> Sperre {
        let r = &self.regeln;
        if !r.aktiv {
            return Sperre::default();
        }
        Sperre {
            listen: r.kategorien.iter().map(|&k| (k, Arc::new(kategorien::laden(app, k)))).collect(),
            eigene: r.eigene.iter().cloned().collect(),
            ausnahmen: r.ausnahmen.iter().cloned().collect(),
            sichere_suche: r.sichere_suche(),
        }
    }
}

pub struct SchutzZustand(pub Mutex<Schutz>);

fn jetzt() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

fn pfad(app: &AppHandle) -> Option<PathBuf> {
    let ordner = app.path().app_config_dir().ok()?;
    std::fs::create_dir_all(&ordner).ok()?;
    Some(ordner.join(DATEI))
}

/// Fehlt die Datei oder ist sie kaputt, gilt: kein Schutz. Eine Datei, die
/// man kaputt machen kann, kann man auch löschen.
fn laden(app: &AppHandle) -> Schutz {
    pfad(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str::<Schutz>(&t).ok())
        .and_then(|s| Some(Schutz { regeln: s.regeln.clone().pruefen().ok()?, ..s }))
        .unwrap_or_default()
}

fn speichern(app: &AppHandle, schutz: &Schutz) -> Result<(), String> {
    let ziel = pfad(app).ok_or("Konfigurationsordner unbekannt")?;
    let teil = ziel.with_extension("json.part");
    let json = serde_json::to_string_pretty(schutz).map_err(|e| e.to_string())?;
    std::fs::write(&teil, json).and_then(|_| std::fs::rename(&teil, &ziel)).map_err(|e| format!("Schutz nicht gespeichert: {e}"))
}

/// Baut die Sperre aus den geltenden Regeln und holt fehlende Listen nach.
fn anwenden(app: &AppHandle) {
    let schutz = app.state::<SchutzZustand>().0.lock().unwrap().clone();
    sperre::setzen(schutz.sperre(app));
    let _ = app.emit_to("main", EREIGNIS, ());
    if !schutz.regeln.aktiv {
        return;
    }
    let (app, kategorien) = (app.clone(), schutz.regeln.kategorien.clone());
    tauri::async_runtime::spawn(async move {
        if kategorien::erneuern(&app, &kategorien).await {
            let schutz = app.state::<SchutzZustand>().0.lock().unwrap().clone();
            let _ = tauri::async_runtime::spawn_blocking(move || sperre::setzen(schutz.sperre(&app))).await;
        }
    });
}

/// Beim Start: Datei lesen, Sperre bauen, jede Sekunde nach fälligen Anträgen sehen.
pub fn starten(app: &AppHandle) {
    app.manage(SchutzZustand(Mutex::new(laden(app))));
    let app = app.clone();
    std::thread::spawn(move || {
        anwenden(&app);
        let zustand = app.state::<SchutzZustand>();
        loop {
            let geaendert = {
                let mut s = zustand.0.lock().unwrap();
                s.faellig_anwenden(jetzt()) && speichern(&app, &s).is_ok()
            };
            if geaendert {
                anwenden(&app);
            }
            std::thread::sleep(Duration::from_secs(1));
        }
    });
}

#[derive(Serialize)]
pub struct AntragStand {
    pub ziel: Regeln,
    pub rest_sekunden: u64,
    pub text: Option<String>,
}

#[derive(Serialize)]
pub struct Stand {
    pub regeln: Regeln,
    pub antrag: Option<AntragStand>,
    pub listen: Vec<kategorien::ListeStand>,
}

fn stand(app: &AppHandle, s: &Schutz) -> Stand {
    let jetzt = jetzt();
    Stand {
        regeln: s.regeln.clone(),
        antrag: s.antrag.as_ref().map(|a| AntragStand {
            ziel: a.ziel.clone(),
            rest_sekunden: a.faellig.saturating_sub(jetzt),
            text: a.text.clone(),
        }),
        listen: kategorien::stand(app),
    }
}

fn aendern_mit(app: &AppHandle, zustand: &SchutzZustand, f: impl FnOnce(&mut Schutz) -> Result<(), String>) -> Result<Stand, String> {
    let s = {
        let mut s = zustand.0.lock().unwrap();
        let mut neu = s.clone();
        f(&mut neu)?;
        speichern(app, &neu)?;
        *s = neu.clone();
        neu
    };
    anwenden(app);
    Ok(stand(app, &s))
}

#[tauri::command(async)]
pub fn schutz_stand(app: AppHandle, zustand: State<'_, SchutzZustand>) -> Stand {
    let schutz = zustand.0.lock().unwrap().clone();
    stand(&app, &schutz)
}

#[tauri::command(async)]
pub fn schutz_aendern(app: AppHandle, zustand: State<'_, SchutzZustand>, regeln: Regeln) -> Result<Stand, String> {
    let regeln = regeln.pruefen()?;
    aendern_mit(&app, &zustand, |s| {
        s.aendern(regeln, jetzt());
        Ok(())
    })
}

/// Nimmt einen offenen Antrag zurück. Das lockert nichts und gilt sofort.
#[tauri::command(async)]
pub fn schutz_abbrechen(app: AppHandle, zustand: State<'_, SchutzZustand>) -> Result<Stand, String> {
    aendern_mit(&app, &zustand, |s| {
        s.antrag = None;
        Ok(())
    })
}

#[tauri::command(async)]
pub fn schutz_bestaetigen(app: AppHandle, zustand: State<'_, SchutzZustand>, text: String) -> Result<Stand, String> {
    aendern_mit(&app, &zustand, |s| s.bestaetigen(&text, jetzt()))
}

#[cfg(test)]
#[path = "schutz_tests.rs"]
mod tests;
