//! Lesezeichen, Verlauf, Top-Sites und Symbole aus `places.sqlite` und
//! `favicons.sqlite` von Firefox.
//!
//! Firefox legt seine Wurzeln als GUIDs ab; für die Oberfläche werden daraus
//! lesbare Ordnernamen. Die Ordnerkette entsteht aus `parent`; ein Zyklus in
//! einer beschädigten Datei beendet nur diesen Eintrag.

use std::collections::{HashMap, HashSet};

use rusqlite::types::ValueRef;
use rusqlite::Connection;

use super::schatten::{oeffnen, Schatten};
use super::{
    gewuenschte_herkuenfte, hat_tabelle, herkunft, kurzer_titel, ordner_name, spalten, warnung, web_adresse, wert_text,
    wert_zahl, zeit_firefox, Gelesen, ImportLesezeichen, ImportTopSite, ImportVerlauf, ImportWarnung, RohBild, BILDER_MAX,
    KNOTEN_MAX, LESEBZEICHEN_MAX, ORDNER_TIEFE_MAX, TOP_SITES_MAX, VERLAUF_MAX,
};

const WURZELN: &[(&str, &str)] = &[
    ("menu________", "Lesezeichenmenü"),
    ("toolbar_____", "Lesezeichenleiste"),
    ("unfiled_____", "Weitere Lesezeichen"),
    ("mobile______", "Mobile Lesezeichen"),
];

struct Knoten {
    typ: i64,
    fk: Option<i64>,
    parent: i64,
    titel: String,
    zeit: i64,
}

pub(crate) fn lesen(schatten: &Schatten) -> Result<Gelesen, &'static str> {
    let mut aus = Gelesen::default();
    let places_pfad = schatten.pfad("places.sqlite");
    if !places_pfad.is_file() {
        return Ok(aus);
    }
    let places = oeffnen(&places_pfad).map_err(|_| "datei_kaputt")?;
    aus.lesezeichen = lesezeichen(&places, &mut aus.warnungen);
    aus.verlauf = verlauf(&places, &mut aus.warnungen);
    aus.top_sites = top_sites(&places);

    let wanted = gewuenschte_herkuenfte(&aus.lesezeichen, &aus.top_sites, &mut aus.warnungen);
    let favicons_pfad = schatten.pfad("favicons.sqlite");
    let favicons_conn = if favicons_pfad.is_file() { oeffnen(&favicons_pfad).ok() } else { None };
    aus.roh_bilder = roh_bilder(favicons_conn.as_ref().unwrap_or(&places), &wanted, &mut aus.warnungen);
    Ok(aus)
}

fn lesezeichen(conn: &Connection, warnungen: &mut Vec<ImportWarnung>) -> Vec<ImportLesezeichen> {
    if !hat_tabelle(conn, "moz_bookmarks") || !hat_tabelle(conn, "moz_places") {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    }
    let mut roots: HashMap<i64, &'static str> = HashMap::new();
    for (guid, label) in WURZELN {
        if let Ok(id) = conn.query_row("SELECT id FROM moz_bookmarks WHERE guid = ?1", [guid], |r| r.get::<_, i64>(0)) {
            roots.insert(id, label);
        }
    }
    if roots.is_empty() {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    }

    let sp = spalten(conn, "moz_bookmarks");
    if !sp.contains("type") || !sp.contains("parent") {
        warnung(warnungen, "schema");
        return Vec::new();
    }
    let position = if sp.contains("position") { "position" } else { "id" };
    let titel = if sp.contains("title") { "COALESCE(title, '')" } else { "''" };
    let zeit = if sp.contains("dateadded") { "COALESCE(dateAdded, 0)" } else { "0" };
    let sql = format!(
        "SELECT id, type, fk, parent, {titel}, {zeit} FROM moz_bookmarks \
         WHERE type IN (1, 2) ORDER BY parent, {position} LIMIT {}",
        KNOTEN_MAX + 1
    );
    let Ok(mut stmt) = conn.prepare(&sql) else {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    };
    let Ok(zeilen) = stmt.query_map([], |r| {
        Ok((
            wert_zahl(r.get_ref(0)?),
            wert_zahl(r.get_ref(1)?),
            match r.get_ref(2)? {
                ValueRef::Null => None,
                wert => Some(wert_zahl(wert)),
            },
            wert_zahl(r.get_ref(3)?),
            wert_text(r.get_ref(4)?),
            wert_zahl(r.get_ref(5)?),
        ))
    }) else {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    };

    let mut knoten: HashMap<i64, Knoten> = HashMap::new();
    let mut reihenfolge = Vec::new();
    for (id, typ, fk, parent, titel, zeit) in zeilen.flatten() {
        if knoten.len() >= KNOTEN_MAX {
            warnung(warnungen, "lesezeichen_gekuerzt");
            break;
        }
        knoten.insert(id, Knoten { typ, fk, parent, titel, zeit });
        reihenfolge.push(id);
    }

    let ids: Vec<i64> = {
        let mut gesehen = HashSet::new();
        knoten
            .values()
            .filter_map(|k| k.fk)
            .filter(|id| gesehen.insert(*id))
            .collect()
    };
    let mut orte: HashMap<i64, String> = HashMap::new();
    for teil in ids.chunks(400) {
        let platzhalter = vec!["?"; teil.len()].join(",");
        let sql = format!("SELECT id, url FROM moz_places WHERE id IN ({platzhalter})");
        let Ok(mut stmt) = conn.prepare(&sql) else { continue };
        if let Ok(zeilen) = stmt.query_map(rusqlite::params_from_iter(teil.iter()), |r| {
            Ok((wert_zahl(r.get_ref(0)?), wert_text(r.get_ref(1)?)))
        }) {
            for (id, url) in zeilen.flatten() {
                orte.insert(id, url);
            }
        };
    }

    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    for id in reihenfolge {
        let Some(k) = knoten.get(&id) else { continue };
        if k.typ != 1 {
            continue;
        }
        let Some(platz) = k.fk.and_then(|fk| orte.get(&fk)) else { continue };
        let Some(url) = web_adresse(platz) else { continue };
        let Some(ordner) = ordnerkette(k.parent, &knoten, &roots) else { continue };
        let key = format!("{url}\u{1f}{}", ordner.join("\u{1f}"));
        if !gesehen.insert(key) {
            continue;
        }
        if aus.len() >= LESEBZEICHEN_MAX {
            warnung(warnungen, "lesezeichen_gekuerzt");
            break;
        }
        aus.push(ImportLesezeichen {
            titel: kurzer_titel(&k.titel, &url),
            url,
            ordner,
            zeit: zeit_firefox(k.zeit).unwrap_or(0),
        });
    }
    aus
}

fn ordnerkette(
    mut aktuell: i64,
    knoten: &HashMap<i64, Knoten>,
    roots: &HashMap<i64, &'static str>,
) -> Option<Vec<String>> {
    let mut teile = Vec::new();
    let mut gesehen = HashSet::new();
    loop {
        if let Some(label) = roots.get(&aktuell) {
            teile.push((*label).to_string());
            break;
        }
        if !gesehen.insert(aktuell) || teile.len() >= ORDNER_TIEFE_MAX {
            return None;
        }
        let k = knoten.get(&aktuell)?;
        if k.parent <= 0 {
            return None;
        }
        if let Some(name) = ordner_name(&k.titel) {
            teile.push(name);
        }
        aktuell = k.parent;
    }
    teile.reverse();
    Some(teile)
}

fn verlauf(conn: &Connection, warnungen: &mut Vec<ImportWarnung>) -> Vec<ImportVerlauf> {
    if !hat_tabelle(conn, "moz_places") {
        warnung(warnungen, "verlauf_kaputt");
        return Vec::new();
    }
    let sp = spalten(conn, "moz_places");
    if !sp.contains("url") || !sp.contains("last_visit_date") {
        warnung(warnungen, "schema");
        return Vec::new();
    }
    let titel = if sp.contains("title") { "COALESCE(title, '')" } else { "''" };
    let anzahl = if sp.contains("visit_count") { "COALESCE(visit_count, 0)" } else { "0" };
    let besucht = if sp.contains("visit_count") { " AND COALESCE(visit_count, 0) > 0" } else { "" };
    let sql = format!(
        "SELECT url, {titel}, {anzahl}, last_visit_date FROM moz_places \
         WHERE url LIKE 'http%'{besucht} AND last_visit_date IS NOT NULL \
         ORDER BY last_visit_date DESC LIMIT {}",
        VERLAUF_MAX + 1
    );
    let Ok(mut stmt) = conn.prepare(&sql) else {
        warnung(warnungen, "verlauf_kaputt");
        return Vec::new();
    };
    let Ok(zeilen) = stmt.query_map([], |r| {
        Ok((
            wert_text(r.get_ref(0)?),
            wert_text(r.get_ref(1)?),
            wert_zahl(r.get_ref(2)?),
            wert_zahl(r.get_ref(3)?),
        ))
    }) else {
        warnung(warnungen, "verlauf_kaputt");
        return Vec::new();
    };

    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    let mut gekuerzt = false;
    for (url, titel, anzahl, zeit) in zeilen.flatten() {
        let Some(url) = web_adresse(&url) else { continue };
        let Some(zeit) = zeit_firefox(zeit) else { continue };
        if !gesehen.insert(url.clone()) {
            continue;
        }
        if aus.len() >= VERLAUF_MAX {
            gekuerzt = true;
            break;
        }
        aus.push(ImportVerlauf {
            titel: kurzer_titel(&titel, &url),
            url,
            zeit,
            anzahl: anzahl.clamp(1, u32::MAX as i64) as u32,
        });
    }
    if gekuerzt {
        warnung(warnungen, "verlauf_gekuerzt");
    }
    aus.sort_by(|a, b| b.zeit.cmp(&a.zeit));
    aus
}

fn top_sites(conn: &Connection) -> Vec<ImportTopSite> {
    if !hat_tabelle(conn, "moz_places") {
        return Vec::new();
    }
    let sp = spalten(conn, "moz_places");
    if !sp.contains("url") {
        return Vec::new();
    }
    let titel = if sp.contains("title") { "COALESCE(title, '')" } else { "''" };
    let rang = if sp.contains("frecency") {
        "frecency"
    } else if sp.contains("visit_count") {
        "visit_count"
    } else {
        "id"
    };
    let besucht = if sp.contains("visit_count") { " AND COALESCE(visit_count, 0) > 0" } else { "" };
    let sql = format!(
        "SELECT url, {titel} FROM moz_places WHERE url LIKE 'http%'{besucht} \
         ORDER BY {rang} DESC LIMIT {}",
        TOP_SITES_MAX + 1
    );
    let Ok(mut stmt) = conn.prepare(&sql) else { return Vec::new() };
    let Ok(zeilen) = stmt.query_map([], |r| Ok((wert_text(r.get_ref(0)?), wert_text(r.get_ref(1)?)))) else {
        return Vec::new();
    };
    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    for (url, titel) in zeilen.flatten() {
        let Some(url) = web_adresse(&url) else { continue };
        if !gesehen.insert(url.clone()) {
            continue;
        }
        if aus.len() >= TOP_SITES_MAX {
            break;
        }
        aus.push(ImportTopSite {
            titel: kurzer_titel(&titel, &url),
            url,
        });
    }
    aus
}

fn roh_bilder(conn: &Connection, wanted: &[String], warnungen: &mut Vec<ImportWarnung>) -> Vec<RohBild> {
    if wanted.is_empty() {
        return Vec::new();
    }
    for tabelle in ["moz_pages_w_icons", "moz_icons_to_pages", "moz_icons"] {
        if !hat_tabelle(conn, tabelle) {
            warnung(warnungen, "bilder_fehlen");
            return Vec::new();
        }
    }
    let gesucht: HashSet<&str> = wanted.iter().map(String::as_str).collect();
    let mut pro_herkunft: HashMap<String, i64> = HashMap::new();
    let Ok(mut stmt) = conn.prepare("SELECT page_url, page_id FROM moz_pages_w_icons LIMIT 100000") else {
        warnung(warnungen, "bilder_kaputt");
        return Vec::new();
    };
    if let Ok(zeilen) = stmt.query_map([], |r| Ok((wert_text(r.get_ref(0)?), wert_zahl(r.get_ref(1)?)))) {
        for (seite, page) in zeilen.flatten() {
            let Some(h) = herkunft(&seite) else { continue };
            if gesucht.contains(h.as_str()) {
                pro_herkunft.entry(h).or_insert(page);
            }
            if pro_herkunft.len() == wanted.len() {
                break;
            }
        }
    }
    drop(stmt);

    let mut aus = Vec::new();
    for h in wanted.iter().take(BILDER_MAX) {
        let Some(page) = pro_herkunft.get(h) else { continue };
        let Ok(mut stmt) = conn.prepare(
            "SELECT i.data FROM moz_icons_to_pages p JOIN moz_icons i ON i.id = p.icon_id \
             WHERE p.page_id = ?1 ORDER BY COALESCE(i.width, 0) DESC LIMIT 8",
        ) else {
            continue;
        };
        let Ok(zeilen) = stmt.query_map([page], |r| {
            Ok(match r.get_ref(0)? {
                ValueRef::Blob(blob) => Some(blob.to_vec()),
                _ => None,
            })
        }) else {
            continue;
        };
        let mut kandidaten = Vec::new();
        for blob in zeilen.flatten().flatten() {
            if blob.is_empty() || blob.len() > 512 * 1024 {
                continue;
            }
            kandidaten.push(blob);
            if kandidaten.len() >= 4 {
                break;
            }
        }
        if !kandidaten.is_empty() {
            aus.push(RohBild {
                herkunft: h.clone(),
                kandidaten,
            });
        }
    }
    aus
}

#[cfg(test)]
mod tests {
    use super::super::testordner;
    use super::*;
    use crate::browserimport::schatten::Schatten;
    use std::fs;
    use std::path::Path;

    fn datei(pfad: &Path, inhalt: &[u8]) {
        fs::create_dir_all(pfad.parent().unwrap()).unwrap();
        fs::write(pfad, inhalt).unwrap();
    }

    fn places(pfad: &Path) {
        let conn = Connection::open(pfad).unwrap();
        conn.execute_batch(
            "CREATE TABLE moz_places (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, last_visit_date INTEGER, frecency INTEGER);
             CREATE TABLE moz_bookmarks (id INTEGER PRIMARY KEY, type INTEGER, fk INTEGER, parent INTEGER, position INTEGER, title TEXT, dateAdded INTEGER, guid TEXT);
             INSERT INTO moz_places VALUES (100, 'https://a.example/', 'A', 4, 1000000, 90);
             INSERT INTO moz_places VALUES (101, 'https://b.example/', 'B', 2, 3000000, 10);
             INSERT INTO moz_places VALUES (102, 'javascript:alert(1)', 'JS', 1, 2000000, 50);
             INSERT INTO moz_bookmarks VALUES (1, 2, NULL, 0, 0, '', 0, 'root________');
             INSERT INTO moz_bookmarks VALUES (2, 2, NULL, 1, 0, 'Menü', 0, 'menu________');
             INSERT INTO moz_bookmarks VALUES (3, 2, NULL, 1, 1, '', 0, 'toolbar_____');
             INSERT INTO moz_bookmarks VALUES (10, 1, 100, 3, 0, 'A', 4000000, NULL);
             INSERT INTO moz_bookmarks VALUES (11, 2, NULL, 3, 1, 'Dev', 0, NULL);
             INSERT INTO moz_bookmarks VALUES (12, 1, 101, 11, 0, 'B', 5000000, NULL);
             INSERT INTO moz_bookmarks VALUES (13, 1, 100, 2, 0, 'A nochmal', 6000000, NULL);
             INSERT INTO moz_bookmarks VALUES (14, 1, 101, 11, 1, 'B doppelt', 7000000, NULL);
             INSERT INTO moz_bookmarks VALUES (20, 2, NULL, 0, 0, 'Zyklus A', 0, NULL);
             INSERT INTO moz_bookmarks VALUES (21, 2, NULL, 20, 0, 'Zyklus B', 0, NULL);
             UPDATE moz_bookmarks SET parent = 21 WHERE id = 20;
             INSERT INTO moz_bookmarks VALUES (22, 1, 100, 20, 0, 'Im Zyklus', 0, NULL);",
        )
        .unwrap();
    }

    #[test]
    fn liest_lesezeichen_mit_wurzeln_ordnern_und_zyklusschutz() {
        let t = testordner("firefox-lesezeichen");
        let profil = t.0.join("Profil");
        datei(&profil.join("places.sqlite"), b"");
        places(&profil.join("places.sqlite"));

        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("places.sqlite"), &profil, "places.sqlite", 1024 * 1024).unwrap();
        let gelesen = lesen(&schatten).unwrap();
        assert_eq!(gelesen.lesezeichen.len(), 3, "zwei Wurzeln, ein Ordner, doppelter Eintrag und Zyklus fallen weg");
        let a = gelesen.lesezeichen.iter().find(|e| e.url == "https://a.example/" && e.ordner == ["Lesezeichenleiste"]).unwrap();
        assert_eq!(a.titel, "A");
        assert!(a.zeit > 0);
        let b = gelesen.lesezeichen.iter().find(|e| e.url == "https://b.example/").unwrap();
        assert_eq!(b.ordner, ["Lesezeichenleiste", "Dev"]);
        assert!(!gelesen.lesezeichen.iter().any(|e| e.titel == "Im Zyklus"));
    }

    #[test]
    fn liest_verlauf_und_top_sites_nach_frecency() {
        let t = testordner("firefox-verlauf");
        let profil = t.0.join("Profil");
        datei(&profil.join("places.sqlite"), b"");
        places(&profil.join("places.sqlite"));
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("places.sqlite"), &profil, "places.sqlite", 1024 * 1024).unwrap();

        let gelesen = lesen(&schatten).unwrap();
        assert_eq!(gelesen.verlauf.iter().map(|e| e.url.as_str()).collect::<Vec<_>>(), [
            "https://b.example/",
            "https://a.example/"
        ]);
        assert_eq!(gelesen.verlauf[0].anzahl, 2);
        assert_eq!(gelesen.top_sites.iter().map(|t| t.url.as_str()).collect::<Vec<_>>(), [
            "https://a.example/",
            "https://b.example/"
        ]);
    }

    #[test]
    fn liest_symbole_aus_favicons_sqlite() {
        let t = testordner("firefox-symbole");
        let profil = t.0.join("Profil");
        datei(&profil.join("places.sqlite"), b"");
        places(&profil.join("places.sqlite"));
        {
            let conn = Connection::open(profil.join("favicons.sqlite")).unwrap();
            conn.execute_batch(
                "CREATE TABLE moz_pages_w_icons (page_url TEXT, page_id INTEGER);
                 CREATE TABLE moz_icons (id INTEGER, width INTEGER, height INTEGER, data BLOB);
                 CREATE TABLE moz_icons_to_pages (page_id INTEGER, icon_id INTEGER);
                 INSERT INTO moz_pages_w_icons VALUES ('https://a.example/seite', 7);
                 INSERT INTO moz_icons VALUES (8, 16, 16, X'05');
                 INSERT INTO moz_icons VALUES (9, 32, 32, X'06');
                 INSERT INTO moz_icons_to_pages VALUES (7, 8);
                 INSERT INTO moz_icons_to_pages VALUES (7, 9);",
            )
            .unwrap();
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("places.sqlite"), &profil, "places.sqlite", 1024 * 1024).unwrap();
        schatten.kopiere(&profil.join("favicons.sqlite"), &profil, "favicons.sqlite", 1024 * 1024).unwrap();

        let gelesen = lesen(&schatten).unwrap();
        let a = gelesen.roh_bilder.iter().find(|b| b.herkunft == "https://a.example").unwrap();
        assert_eq!(a.kandidaten[0], vec![6]);
        assert_eq!(a.kandidaten[1], vec![5]);
    }

    #[test]
    fn eine_kaputte_places_datei_ist_ein_fehler() {
        let t = testordner("firefox-kaputt");
        let profil = t.0.join("Profil");
        datei(&profil.join("places.sqlite"), b"kein sqlite");
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("places.sqlite"), &profil, "places.sqlite", 1024 * 1024).unwrap();
        assert_eq!(lesen(&schatten), Err("datei_kaputt"));
    }

    #[test]
    fn fehlende_tabellen_sind_eine_warnung() {
        let t = testordner("firefox-schema");
        let profil = t.0.join("Profil");
        datei(&profil.join("places.sqlite"), b"");
        Connection::open(profil.join("places.sqlite")).unwrap().execute_batch("CREATE TABLE fremd (x);").unwrap();
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("places.sqlite"), &profil, "places.sqlite", 1024 * 1024).unwrap();
        let gelesen = lesen(&schatten).unwrap();
        assert!(gelesen.lesezeichen.is_empty());
        assert!(gelesen.warnungen.iter().any(|w| w.code == "lesezeichen_kaputt"));
        assert!(gelesen.warnungen.iter().any(|w| w.code == "verlauf_kaputt"));
    }
}
