use super::*;
use crate::schild::schutz_dienst::{lesen, stand_von};

/// Netzzeit des Antrags.
const T: u64 = 1_800_000_000;
const H: u64 = 3600;

fn an(kategorien: &[Kategorie]) -> Regeln {
    Regeln { aktiv: true, kategorien: kategorien.to_vec(), ..Regeln::default() }
}

fn schutz(regeln: Regeln) -> Schutz {
    Schutz { regeln, ..Schutz::default() }
}

#[test]
fn verschaerfen_gilt_sofort_auch_ohne_netz() {
    let mut s = Schutz::default();
    s.aendern(an(&[Kategorie::Erwachsene]), T, None).unwrap();
    assert_eq!(s.regeln, an(&[Kategorie::Erwachsene]));
    assert!(s.antrag.is_none(), "ohne Schutz gibt es nichts zu warten");

    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Shopping]), T, None).unwrap();
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Shopping]);
    // Längere Wartezeit ist strenger.
    s.aendern(Regeln { wartezeit_stunden: 168, ..an(&[Kategorie::Erwachsene, Kategorie::Shopping]) }, T, None).unwrap();
    assert_eq!(s.regeln.wartezeit_stunden, 168);
    assert!(s.antrag.is_none());
}

#[test]
fn lockern_wird_ein_antrag_und_gilt_nie_von_selbst() {
    let mut s = schutz(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel]));
    s.aendern(an(&[Kategorie::Erwachsene]), T, Some(T)).unwrap();
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Gluecksspiel], "noch gesperrt");
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 24 * H);

    // Der Ablauf wendet nichts an, er lässt nur verfallen.
    assert!(!s.ablaufen(T + 24 * H));
    assert_eq!(s.regeln.kategorien.len(), 2);
    assert!(s.ablaufen(T + 25 * H));
    assert!(s.antrag.is_none());
    assert_eq!(s.regeln.kategorien.len(), 2);
    assert_eq!(s.naechster_antrag_ab, T + 25 * H + ABKUEHLEN);
}

#[test]
fn bestaetigen_nur_im_fenster_und_nur_mit_netzzeit() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    s.aendern(Regeln { aktiv: false, ..an(&[Kategorie::Gluecksspiel]) }, T, Some(T)).unwrap();
    let faellig = T + 24 * H;

    assert_eq!(s.bestaetigen(None).unwrap_err(), "ohne_netz");
    assert_eq!(s.bestaetigen(Some(faellig - 1)).unwrap_err(), "zu_frueh");
    assert!(s.regeln.aktiv);
    let mut spaet = s.clone();
    s.bestaetigen(Some(faellig + FENSTER - 1)).unwrap();
    assert!(!s.regeln.aktiv);
    assert!(s.antrag.is_none());

    // Eine Sekunde zu spät: der Antrag ist weg, und einen Tag lang gibt es keinen neuen.
    assert_eq!(spaet.bestaetigen(Some(faellig + FENSTER)).unwrap_err(), "verfallen");
    assert!(spaet.regeln.aktiv && spaet.antrag.is_none());
    assert_eq!(spaet.naechster_antrag_ab, faellig + FENSTER + ABKUEHLEN);
    assert_eq!(spaet.bestaetigen(Some(faellig + FENSTER)).unwrap_err(), "kein_antrag");
}

#[test]
fn ohne_netz_laesst_sich_nichts_beantragen_und_nichts_aendert_sich() {
    let alt = schutz(an(&[Kategorie::Gluecksspiel]));
    let mut s = alt.clone();
    // Lockern mit Verschärfen gemischt: auch der strenge Teil bleibt liegen, die Oberfläche sagt es.
    assert_eq!(s.aendern(an(&[Kategorie::Shopping]), T, None).unwrap_err(), "ohne_netz");
    assert_eq!(s, alt);
}

#[test]
fn jede_art_zu_lockern_wartet() {
    let basis = Regeln { eigene: vec!["zeit.example".into()], wartezeit_stunden: 72, ..an(&[Kategorie::Sozial]) };
    let faelle = [
        Regeln { aktiv: false, ..basis.clone() },
        Regeln { kategorien: vec![], ..basis.clone() },
        Regeln { eigene: vec![], ..basis.clone() },
        Regeln { ausnahmen: vec!["reddit.com".into()], ..basis.clone() },
        Regeln { wartezeit_stunden: 24, ..basis.clone() },
    ];
    for neu in faelle {
        let mut s = schutz(basis.clone());
        s.aendern(neu.clone(), T, Some(T)).unwrap();
        assert_eq!(s.regeln, basis, "sofort gelockert: {neu:?}");
        // Gewartet wird mit der bisherigen Wartezeit, auch wenn sie gerade verkürzt wird.
        assert_eq!(s.antrag.unwrap().faellig, T + 72 * H, "{neu:?}");
    }
}

#[test]
fn gemischte_aenderung_verschaerft_sofort_und_lockert_spaeter() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    s.aendern(an(&[Kategorie::Shopping]), T, Some(T)).unwrap();
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Gluecksspiel, Kategorie::Shopping]);
    s.bestaetigen(Some(T + 24 * H)).unwrap();
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Shopping]);
}

#[test]
fn ein_offener_antrag_wird_nicht_still_lockerer() {
    let mut s = schutz(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel, Kategorie::Sozial]));
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel]), T, Some(T)).unwrap();
    // Zehn Minuten später noch etwas Strengeres dazu: die Frist bleibt, auch ohne Netz.
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel, Kategorie::Shopping]), T + 600, None).unwrap();
    assert!(s.regeln.kategorien.contains(&Kategorie::Shopping));
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 24 * H);
    // Noch etwas lockern: die Wartezeit beginnt von vorn.
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Shopping]), T + 700, Some(T + 700)).unwrap();
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 700 + 24 * H);
    assert_eq!(s.bestaetigen(Some(T + 24 * H)).unwrap_err(), "zu_frueh");
    s.bestaetigen(Some(T + 700 + 24 * H)).unwrap();
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Shopping]);
}

#[test]
fn den_wunsch_zuruecknehmen_raeumt_den_antrag_ab() {
    let alt = an(&[Kategorie::Gluecksspiel]);
    let mut s = schutz(alt.clone());
    s.aendern(Regeln { aktiv: false, ..alt.clone() }, T, Some(T)).unwrap();
    assert!(s.antrag.is_some());
    s.aendern(alt.clone(), T + 10, None).unwrap();
    assert!(s.antrag.is_none());
    assert_eq!(s.regeln, alt);
}

#[test]
fn waehrend_der_bindung_laesst_sich_nichts_beantragen() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    s.binden(7, T).unwrap();
    assert_eq!(s.gebunden_bis, T + 7 * 24 * H);
    let vorher = s.clone();
    assert_eq!(s.aendern(an(&[]), T + 6 * 24 * H, Some(T + 6 * 24 * H)).unwrap_err(), "gebunden");
    assert_eq!(s, vorher);
    // Verschärfen geht.
    s.aendern(an(&[Kategorie::Gluecksspiel, Kategorie::Sozial]), T, None).unwrap();
    // Kürzer binden verlängert nicht und verkürzt nicht.
    s.binden(1, T).unwrap();
    assert_eq!(s.gebunden_bis, T + 7 * 24 * H);
    s.binden(30, T + H).unwrap();
    assert_eq!(s.gebunden_bis, T + H + 30 * 24 * H);
    // Danach wieder ein Antrag.
    let danach = s.gebunden_bis;
    s.aendern(an(&[Kategorie::Sozial]), T, Some(danach)).unwrap();
    assert!(s.antrag.is_some());
    // Binden nimmt einen offenen Antrag zurück.
    s.binden(1, danach).unwrap();
    assert!(s.antrag.is_none());
}

#[test]
fn ohne_bindung_laesst_sich_der_schutz_sofort_wieder_ausschalten() {
    let mut s = Schutz::default();
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Sozial]), T, None).unwrap();
    // Weniger Kategorien und aus, auch ohne Netz und ohne Antrag.
    s.aendern(an(&[Kategorie::Erwachsene]), T, None).unwrap();
    assert_eq!(s.regeln, an(&[Kategorie::Erwachsene]));
    s.aendern(Regeln { aktiv: false, ..an(&[Kategorie::Erwachsene]) }, T, None).unwrap();
    assert!(!s.regeln.aktiv);
    assert!(s.antrag.is_none());

    // Nach einer Bindung wird wieder beantragt, auch wenn sie abgelaufen ist.
    s.aendern(an(&[Kategorie::Erwachsene]), T, None).unwrap();
    s.binden(1, T).unwrap();
    let danach = T + 24 * H;
    s.aendern(Regeln { aktiv: false, ..an(&[Kategorie::Erwachsene]) }, danach, Some(danach)).unwrap();
    assert!(s.regeln.aktiv);
    assert!(s.antrag.is_some());
    s.bestaetigen(Some(danach + 24 * H)).unwrap();
    assert!(!s.regeln.aktiv);

    // Wieder eingeschaltet, ist man wieder frei, bis man bindet.
    s.aendern(an(&[Kategorie::Sozial]), danach, None).unwrap();
    s.aendern(an(&[]), danach, None).unwrap();
    assert_eq!(s.regeln, an(&[]));
}

#[test]
fn eine_alte_datei_ist_frei_nur_wenn_nie_gebunden_wurde() {
    let (mut nie, _) = lesen(Some(r#"{"regeln":{"aktiv":true,"kategorien":["sozial"]},"gebunden_bis":0}"#));
    nie.aendern(an(&[]), T, None).unwrap();
    assert_eq!(nie.regeln, an(&[]));

    let (mut einmal, _) = lesen(Some(r#"{"regeln":{"aktiv":true,"kategorien":["sozial"]},"gebunden_bis":5}"#));
    einmal.aendern(an(&[]), T, Some(T)).unwrap();
    assert_eq!(einmal.regeln, an(&[Kategorie::Sozial]));
    assert!(einmal.antrag.is_some());

    // Steht die Marke da, gilt sie, auch bei 0.
    let (mut markiert, _) = lesen(Some(r#"{"regeln":{"aktiv":true,"kategorien":["sozial"]},"ungebunden":false}"#));
    markiert.aendern(an(&[]), T, Some(T)).unwrap();
    assert!(markiert.antrag.is_some());
    // Kaputt heißt alles gesperrt und nicht frei.
    let (mut kaputt, _) = lesen(Some("{"));
    kaputt.aendern(Regeln::default(), T, Some(T)).unwrap();
    assert!(kaputt.regeln.aktiv);
}

#[test]
fn binden_nur_mit_schutz_und_bekannter_dauer() {
    assert!(Schutz::default().binden(7, T).is_err());
    assert!(schutz(an(&[])).binden(2, T).is_err());
    assert!(schutz(an(&[])).binden(0, T).is_err());
}

#[test]
fn nach_abbruch_einen_tag_kein_neuer_antrag() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    // Ohne Antrag kostet Abbrechen nichts.
    s.abbrechen(T);
    assert_eq!(s.naechster_antrag_ab, 0);
    s.aendern(an(&[]), T, Some(T)).unwrap();
    s.abbrechen(T + 60);
    assert!(s.antrag.is_none());
    assert_eq!(s.aendern(an(&[]), T + 60, Some(T + 60 + ABKUEHLEN - 1)).unwrap_err(), "abkuehlen");
    s.aendern(an(&[]), T + 60, Some(T + 60 + ABKUEHLEN)).unwrap();
    assert!(s.antrag.is_some());
}

#[test]
fn die_serie_zaehlt_tage_ohne_treffer_und_behaelt_den_rekord() {
    let mut s = Schutz::default();
    s.aendern(an(&[Kategorie::Erwachsene]), T, None).unwrap();
    assert_eq!(s.serie.seit, T, "beginnt mit dem Einschalten");
    assert_eq!(s.serie.tage(T + 3 * 24 * H + 5), 3);
    assert!(s.serie.treffer(T + 3 * 24 * H + 5));
    assert_eq!((s.serie.tage(T + 3 * 24 * H + 5), s.serie.rekord_tage), (0, 3));
    // Ein zweiter Treffer kurz danach schreibt nichts.
    assert!(!s.serie.treffer(T + 3 * 24 * H + 30));
    // Eine längere Serie wird der neue Rekord.
    assert_eq!(s.serie.rekord(T + 10 * 24 * H), 6);
    // Wieder einschalten beginnt von vorn, der Rekord bleibt.
    s.aendern(Regeln { aktiv: false, ..an(&[Kategorie::Erwachsene]) }, T, None).unwrap();
    s.aendern(an(&[Kategorie::Erwachsene]), T + 40 * 24 * H, None).unwrap();
    assert_eq!((s.serie.tage(T + 40 * 24 * H), s.serie.rekord_tage), (0, 3));
}

#[test]
fn die_oberflaeche_schickt_nur_gueltiges() {
    assert!(Regeln { wartezeit_stunden: 1, ..Regeln::default() }.pruefen().is_err());
    assert!(Regeln { eigene: vec!["kein host".into()], ..Regeln::default() }.pruefen().is_err());
    assert!(Regeln { ausnahmen: vec!["x.de".into(); HOSTS_MAX + 1], ..Regeln::default() }.pruefen().is_err());
    let r = Regeln {
        kategorien: vec![Kategorie::Sozial, Kategorie::Erwachsene, Kategorie::Sozial],
        eigene: vec![" *.Zeit.Example ".into(), "zeit.example".into()],
        ..Regeln::default()
    }
    .pruefen()
    .unwrap();
    assert_eq!(r.kategorien, vec![Kategorie::Erwachsene, Kategorie::Sozial]);
    assert_eq!(r.eigene, vec!["zeit.example".to_string()]);
}

#[test]
fn sichere_suche_nur_mit_erwachsene() {
    assert!(an(&[Kategorie::Erwachsene]).sichere_suche());
    assert!(!an(&[Kategorie::Sozial]).sichere_suche());
    assert!(!Regeln { aktiv: false, ..an(&[Kategorie::Erwachsene]) }.sichere_suche());
}

#[test]
fn antrag_bindung_und_serie_ueberleben_den_neustart() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    s.aendern(an(&[]), T, Some(T)).unwrap();
    s.naechster_antrag_ab = T + 5;
    s.gebunden_bis = T + 7;
    s.serie = Serie { seit: T - 9, rekord_tage: 4 };
    let (gelesen, beschaedigt) = lesen(Some(&serde_json::to_string(&s).unwrap()));
    assert_eq!(gelesen, s);
    assert!(!beschaedigt);
}

/// Eine Datei von vor dem 08.10.2026 (Hürde statt Wartezeit, Antrag mit
/// `text`): der Schutz bleibt an, gewartet wird jetzt einen Tag.
#[test]
fn eine_alte_datei_bleibt_gueltig() {
    let alt = r#"{
      "regeln": { "aktiv": true, "kategorien": ["erwachsene", "gluecksspiel"], "eigene": [], "ausnahmen": [],
                  "huerde": { "art": "countdown", "minuten": 15 } },
      "antrag": { "ziel": { "aktiv": false, "kategorien": [], "eigene": [], "ausnahmen": [],
                            "huerde": { "art": "abtippen" } }, "faellig": 1800000900, "text": null }
    }"#;
    let (s, beschaedigt) = lesen(Some(alt));
    assert!(!beschaedigt);
    assert_eq!(s.regeln, an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel]));
    assert_eq!(s.regeln.wartezeit_stunden, 24);
    // Der alte Antrag galt von selbst; jetzt muss er bestätigt werden.
    assert_eq!(s.antrag.as_ref().unwrap().faellig, 1_800_000_900);
}

#[test]
fn fehlt_die_datei_ist_der_schutz_aus_ist_sie_kaputt_ist_alles_gesperrt() {
    assert_eq!(lesen(None), (Schutz::default(), false));
    for kaputt in [
        "",
        "{",
        "null",
        r#"{"regeln":{"aktiv":"ja"}}"#,
        r#"{"regeln":{"kategorien":["unbekannt"]}}"#,
        r#"{"regeln":{"wartezeit_stunden":1}}"#,
    ] {
        let (s, beschaedigt) = lesen(Some(kaputt));
        assert!(beschaedigt, "{kaputt}");
        assert_eq!(s.regeln, Regeln::streng(), "{kaputt}");
    }
}

#[test]
fn der_stand_zaehlt_bis_zum_fenster_und_im_fenster() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel]));
    s.aendern(an(&[]), T, Some(T)).unwrap();
    let a = |zeit| stand_von(&s, zeit, zeit).antrag.unwrap();
    assert_eq!((a(T).rest_sekunden, a(T).fenster_sekunden), (24 * H, H));
    assert_eq!((a(T + 24 * H + 600).rest_sekunden, a(T + 24 * H + 600).fenster_sekunden), (0, H - 600));
    assert_eq!(a(T + 30 * H).fenster_sekunden, 0);
}

#[test]
fn die_oberflaeche_bietet_dieselben_grenzen_an() {
    let oberflaeche = include_str!("../../../../frontend/src/browser/einstellungen/Jugendschutz.tsx");
    let wartezeiten = WARTEZEITEN.map(|w| format!("'{w}'")).join(", ");
    assert!(oberflaeche.contains(&format!("const WARTEZEITEN = [{wartezeiten}] as const")), "WARTEZEITEN in Jugendschutz.tsx");
    assert!(oberflaeche.contains(&format!("const HOSTS_MAX = {HOSTS_MAX}\n")), "HOSTS_MAX in Jugendschutz.tsx");
}
