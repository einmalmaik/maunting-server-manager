use super::*;

const T: u64 = 1_800_000_000;

fn an(kategorien: &[Kategorie], minuten: u32) -> Regeln {
    Regeln { aktiv: true, kategorien: kategorien.to_vec(), huerde: Huerde::Countdown { minuten }, ..Regeln::default() }
}

fn schutz(regeln: Regeln) -> Schutz {
    Schutz { regeln, antrag: None }
}

#[test]
fn verschaerfen_gilt_sofort() {
    let mut s = Schutz::default();
    s.aendern(an(&[Kategorie::Erwachsene], 60), T);
    assert_eq!(s.regeln, an(&[Kategorie::Erwachsene], 60));
    assert!(s.antrag.is_none(), "ohne Schutz gibt es nichts zu warten");

    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Shopping], 60), T);
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Shopping]);
    assert!(s.antrag.is_none());
    // Längere Wartezeit ist strenger.
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Shopping], 24 * 60), T);
    assert_eq!(s.regeln.huerde, Huerde::Countdown { minuten: 24 * 60 });
}

#[test]
fn lockern_greift_erst_nach_der_wartezeit() {
    let mut s = schutz(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel], 15));
    s.aendern(an(&[Kategorie::Erwachsene], 15), T);
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Gluecksspiel], "noch gesperrt");
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 15 * 60);

    assert!(!s.faellig_anwenden(T + 15 * 60 - 1));
    assert_eq!(s.regeln.kategorien.len(), 2);
    assert!(s.faellig_anwenden(T + 15 * 60));
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene]);
    assert!(s.antrag.is_none());
}

#[test]
fn jede_art_zu_lockern_wartet() {
    let basis = Regeln { eigene: vec!["zeit.example".into()], ..an(&[Kategorie::Sozial], 60) };
    let faelle = [
        Regeln { aktiv: false, ..basis.clone() },
        Regeln { kategorien: vec![], ..basis.clone() },
        Regeln { eigene: vec![], ..basis.clone() },
        Regeln { ausnahmen: vec!["reddit.com".into()], ..basis.clone() },
        Regeln { huerde: Huerde::Countdown { minuten: 5 }, ..basis.clone() },
        Regeln { huerde: Huerde::Abtippen, ..basis.clone() },
    ];
    for neu in faelle {
        let mut s = schutz(basis.clone());
        s.aendern(neu.clone(), T);
        assert_eq!(s.regeln, basis, "sofort gelockert: {neu:?}");
        // Gewartet wird mit der bisherigen Hürde, auch wenn sie gerade verkürzt wird.
        assert_eq!(s.antrag.unwrap().faellig, T + 60 * 60, "{neu:?}");
    }
}

#[test]
fn gemischte_aenderung_verschaerft_sofort_und_lockert_spaeter() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel], 5));
    s.aendern(an(&[Kategorie::Shopping], 5), T);
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Gluecksspiel, Kategorie::Shopping]);
    assert!(s.faellig_anwenden(T + 5 * 60));
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Shopping]);
}

#[test]
fn ein_offener_antrag_wird_nicht_still_lockerer() {
    let mut s = schutz(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel, Kategorie::Sozial], 15));
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel], 15), T);
    // Zehn Minuten später noch etwas Strengeres dazu: die Frist bleibt.
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Gluecksspiel, Kategorie::Shopping], 15), T + 600);
    assert!(s.regeln.kategorien.contains(&Kategorie::Shopping));
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 15 * 60);
    // Noch etwas lockern: die Wartezeit beginnt von vorn.
    s.aendern(an(&[Kategorie::Erwachsene, Kategorie::Shopping], 15), T + 700);
    assert_eq!(s.antrag.as_ref().unwrap().faellig, T + 700 + 15 * 60);
    assert!(!s.faellig_anwenden(T + 15 * 60));
    assert!(s.faellig_anwenden(T + 700 + 15 * 60));
    assert_eq!(s.regeln.kategorien, vec![Kategorie::Erwachsene, Kategorie::Shopping]);
}

#[test]
fn den_wunsch_zuruecknehmen_raeumt_den_antrag_ab() {
    let alt = an(&[Kategorie::Gluecksspiel], 15);
    let mut s = schutz(alt.clone());
    s.aendern(Regeln { aktiv: false, ..alt.clone() }, T);
    assert!(s.antrag.is_some());
    s.aendern(alt.clone(), T + 10);
    assert!(s.antrag.is_none());
    assert_eq!(s.regeln, alt);
}

#[test]
fn abtippen_braucht_den_text_und_seine_zeit() {
    let alt = Regeln { huerde: Huerde::Abtippen, ..an(&[Kategorie::Gluecksspiel], 15) };
    let mut s = schutz(alt.clone());
    s.aendern(Regeln { aktiv: false, ..alt.clone() }, T);
    let antrag = s.antrag.clone().unwrap();
    let text = antrag.text.clone().unwrap();
    assert!(text.split(' ').count() == WOERTER && text.chars().all(|c| c == ' ' || "bdfgklmnprstvwaeiou".contains(c)));
    assert!(antrag.faellig >= T + 20, "{}", antrag.faellig);

    assert!(!s.faellig_anwenden(T + 999_999), "Abtippen läuft nie von selbst ab");
    assert!(s.bestaetigen("falsch", antrag.faellig).is_err());
    assert!(s.bestaetigen(&text, antrag.faellig - 1).is_err(), "eingefügt statt getippt");
    assert!(s.regeln.aktiv);
    s.bestaetigen(&format!("  {}  ", text.replace(' ', "\n")), antrag.faellig).unwrap();
    assert!(!s.regeln.aktiv);
    assert_ne!(abtipptext(), text, "jedes Mal ein anderer Text");
}

#[test]
fn die_oberflaeche_schickt_nur_gueltiges() {
    assert!(Regeln { huerde: Huerde::Countdown { minuten: 1 }, ..Regeln::default() }.pruefen().is_err());
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
    assert!(an(&[Kategorie::Erwachsene], 5).sichere_suche());
    assert!(!an(&[Kategorie::Sozial], 5).sichere_suche());
    assert!(!Regeln { aktiv: false, ..an(&[Kategorie::Erwachsene], 5) }.sichere_suche());
}

#[test]
fn ein_antrag_ueberlebt_den_neustart() {
    let mut s = schutz(an(&[Kategorie::Gluecksspiel], 60));
    s.aendern(an(&[], 60), T);
    let gelesen: Schutz = serde_json::from_str(&serde_json::to_string(&s).unwrap()).unwrap();
    assert_eq!(gelesen, s);
    assert_eq!(gelesen.antrag.unwrap().faellig, T + 3600);
}
