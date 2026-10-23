//! Lesezeichen, Verlauf, Top-Sites und Symbole aus Chromium-Profilen
//! (Chrome, Brave, Edge, Vivaldi, Opera).
//!
//! Alle Abfragen laufen auf den Kopien aus `schatten.rs`. Tabellen und Spalten
//! werden vorher geprüft: eine neuere Browserfassung darf den Import nicht
//! ganz scheitern lassen, nur weil ein Teil fehlt.

use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use serde_json::Value;

use super::schatten::{oeffnen, Schatten};
use super::{
    gewuenschte_herkuenfte, hat_tabelle, herkunft, kurzer_titel, ordner_name, spalten, warnung, web_adresse, wert_text,
    wert_zahl, zeit_webkit, Gelesen, ImportLesezeichen, ImportTopSite, ImportVerlauf, ImportWarnung, RohBild, BILDER_MAX,
    KNOTEN_MAX, LESEBZEICHEN_MAX, ORDNER_TIEFE_MAX, TOP_SITES_MAX, VERLAUF_MAX,
};

const WURZELN: &[(&str, &str)] = &[
    ("bookmark_bar", "Lesezeichenleiste"),
    ("other", "Weitere Lesezeichen"),
    ("synced", "Synchronisierte Lesezeichen"),
];

pub(crate) fn lesen(schatten: &Schatten) -> Gelesen {
    let mut aus = Gelesen::default();

    let history_pfad = schatten.pfad("History");
    let history_conn = if history_pfad.is_file() {
        match oeffnen(&history_pfad) {
            Ok(conn) => Some(conn),
            Err(_) => {
                warnung(&mut aus.warnungen, "verlauf_kaputt");
                None
            }
        }
    } else {
        None
    };

    if let Some(conn) = history_conn.as_ref() {
        aus.verlauf = verlauf(conn, &mut aus.warnungen);
    }

    let top_pfad = schatten.pfad("Top Sites");
    let top_conn = if top_pfad.is_file() { oeffnen(&top_pfad).ok() } else { None };
    aus.top_sites = top_sites(top_conn.as_ref().or(history_conn.as_ref()), &aus.verlauf);

    aus.lesezeichen = lesezeichen(&schatten.pfad("Bookmarks"), &mut aus.warnungen);

    let wanted = gewuenschte_herkuenfte(&aus.lesezeichen, &aus.top_sites, &mut aus.warnungen);
    let favicons_pfad = schatten.pfad("Favicons");
    let favicons_conn = if favicons_pfad.is_file() { oeffnen(&favicons_pfad).ok() } else { None };
    aus.roh_bilder = roh_bilder(
        favicons_conn.as_ref().or(history_conn.as_ref()),
        &wanted,
        &mut aus.warnungen,
    );
    aus
}

fn verlauf(conn: &Connection, warnungen: &mut Vec<ImportWarnung>) -> Vec<ImportVerlauf> {
    if !hat_tabelle(conn, "urls") {
        warnung(warnungen, "verlauf_kaputt");
        return Vec::new();
    }
    let sp = spalten(conn, "urls");
    if !sp.contains("url") || !sp.contains("last_visit_time") {
        warnung(warnungen, "schema");
        return Vec::new();
    }
    let titel = if sp.contains("title") { "title" } else { "''" };
    let anzahl = if sp.contains("visit_count") { "visit_count" } else { "0" };
    let versteckt = if sp.contains("hidden") { " AND (hidden = 0 OR hidden IS NULL)" } else { "" };
    let sql = format!(
        "SELECT url, {titel}, {anzahl}, last_visit_time FROM urls WHERE url LIKE 'http%'{versteckt} \
         ORDER BY last_visit_time DESC LIMIT {}",
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
        let Some(zeit) = zeit_webkit(zeit) else { continue };
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

fn top_sites(conn: Option<&Connection>, verlauf: &[ImportVerlauf]) -> Vec<ImportTopSite> {
    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();

    if let Some(conn) = conn {
        if hat_tabelle(conn, "top_sites") {
            let sp = spalten(conn, "top_sites");
            if sp.contains("url") {
                let titel = if sp.contains("title") { "title" } else { "''" };
                let reihenfolge = if sp.contains("url_rank") { " ORDER BY url_rank" } else { "" };
                let sql = format!("SELECT url, {titel} FROM top_sites{reihenfolge} LIMIT {}", TOP_SITES_MAX + 1);
                if let Ok(mut stmt) = conn.prepare(&sql) {
                    if let Ok(zeilen) = stmt.query_map([], |r| Ok((wert_text(r.get_ref(0)?), wert_text(r.get_ref(1)?)))) {
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
                    }
                }
            }
        }
    }

    // Ohne Top-Sites-Datei sind die meistbesuchten Seiten des Verlaufs die
    // nächste Näherung.
    if aus.is_empty() {
        let mut sortiert: Vec<&ImportVerlauf> = verlauf.iter().collect();
        sortiert.sort_by(|a, b| b.anzahl.cmp(&a.anzahl).then_with(|| b.zeit.cmp(&a.zeit)));
        for eintrag in sortiert {
            if aus.len() >= TOP_SITES_MAX {
                break;
            }
            if !gesehen.insert(eintrag.url.clone()) {
                continue;
            }
            aus.push(ImportTopSite {
                url: eintrag.url.clone(),
                titel: eintrag.titel.clone(),
            });
        }
    }
    aus
}

fn lesezeichen(datei: &Path, warnungen: &mut Vec<ImportWarnung>) -> Vec<ImportLesezeichen> {
    let Ok(bytes) = fs::read(datei) else { return Vec::new() };
    let Ok(wert) = serde_json::from_slice::<Value>(&bytes) else {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    };
    let Some(roots) = wert.get("roots").and_then(Value::as_object) else {
        warnung(warnungen, "lesezeichen_kaputt");
        return Vec::new();
    };
    let mut aus = Vec::new();
    let mut gesehen = HashSet::new();
    let mut knoten = 0usize;
    for (schluessel, label) in WURZELN {
        if let Some(wurzel) = roots.get(*schluessel) {
            gehe(
                wurzel,
                &[label.to_string()],
                0,
                &mut aus,
                &mut gesehen,
                &mut knoten,
                warnungen,
            );
        }
    }
    aus
}

#[allow(clippy::too_many_arguments)]
fn gehe(
    wert: &Value,
    pfad: &[String],
    tiefe: usize,
    aus: &mut Vec<ImportLesezeichen>,
    gesehen: &mut HashSet<String>,
    knoten: &mut usize,
    warnungen: &mut Vec<ImportWarnung>,
) {
    if *knoten >= KNOTEN_MAX {
        warnung(warnungen, "lesezeichen_gekuerzt");
        return;
    }
    *knoten += 1;

    let art = wert.get("type").and_then(Value::as_str).unwrap_or("");
    if art == "url" {
        if aus.len() >= LESEBZEICHEN_MAX {
            warnung(warnungen, "lesezeichen_gekuerzt");
            return;
        }
        let Some(url) = wert.get("url").and_then(Value::as_str).and_then(web_adresse) else { return };
        let key = format!("{url}\u{1f}{}", pfad.join("\u{1f}"));
        if !gesehen.insert(key) {
            return;
        }
        let titel = wert.get("name").and_then(Value::as_str).unwrap_or("");
        aus.push(ImportLesezeichen {
            titel: kurzer_titel(titel, &url),
            url,
            ordner: pfad.to_vec(),
            zeit: wert.get("date_added").and_then(zahl_aus_wert).and_then(zeit_webkit).unwrap_or(0),
        });
        return;
    }

    if art != "folder" && wert.get("children").is_none() {
        return;
    }
    if tiefe > ORDNER_TIEFE_MAX {
        warnung(warnungen, "lesezeichen_gekuerzt");
        return;
    }

    let mut neuer_pfad = pfad.to_vec();
    if tiefe > 0 {
        if let Some(name) = wert.get("name").and_then(Value::as_str).and_then(ordner_name) {
            neuer_pfad.push(name);
        }
    }
    let Some(kinder) = wert.get("children").and_then(Value::as_array) else { return };
    for kind in kinder {
        gehe(kind, &neuer_pfad, tiefe + 1, aus, gesehen, knoten, warnungen);
    }
}

fn zahl_aus_wert(wert: &Value) -> Option<i64> {
    match wert {
        Value::String(text) => text.parse().ok(),
        Value::Number(zahl) => zahl.as_i64(),
        _ => None,
    }
}

fn roh_bilder(conn: Option<&Connection>, wanted: &[String], warnungen: &mut Vec<ImportWarnung>) -> Vec<RohBild> {
    let Some(conn) = conn else { return Vec::new() };
    if wanted.is_empty() {
        return Vec::new();
    }
    if !hat_tabelle(conn, "icon_mapping") || !hat_tabelle(conn, "favicon_bitmaps") {
        warnung(warnungen, "bilder_fehlen");
        return Vec::new();
    }
    let gesucht: HashSet<&str> = wanted.iter().map(String::as_str).collect();
    let mut pro_herkunft: HashMap<String, i64> = HashMap::new();
    let Ok(mut stmt) = conn.prepare("SELECT page_url, icon_id FROM icon_mapping LIMIT 100000") else {
        warnung(warnungen, "bilder_kaputt");
        return Vec::new();
    };
    if let Ok(zeilen) = stmt.query_map([], |r| Ok((wert_text(r.get_ref(0)?), wert_zahl(r.get_ref(1)?)))) {
        for (seite, icon) in zeilen.flatten() {
            let Some(h) = herkunft(&seite) else { continue };
            if gesucht.contains(h.as_str()) {
                pro_herkunft.entry(h).or_insert(icon);
            }
            if pro_herkunft.len() == wanted.len() {
                break;
            }
        }
    }
    drop(stmt);

    let mut aus = Vec::new();
    for h in wanted.iter().take(BILDER_MAX) {
        let Some(icon) = pro_herkunft.get(h) else { continue };
        let Ok(mut stmt) = conn.prepare(
            "SELECT image_data FROM favicon_bitmaps WHERE icon_id = ?1 \
             ORDER BY COALESCE(width, 0) DESC, COALESCE(height, 0) DESC LIMIT 8",
        ) else {
            continue;
        };
        let Ok(zeilen) = stmt.query_map([icon], |r| {
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
    use super::super::WEBKIT_OFFSET_MS;
    use super::*;
    use crate::browserimport::schatten::Schatten;

    fn datei(pfad: &Path, inhalt: &[u8]) {
        fs::create_dir_all(pfad.parent().unwrap()).unwrap();
        fs::write(pfad, inhalt).unwrap();
    }

    fn webkit(ms: i64) -> i64 {
        (ms + WEBKIT_OFFSET_MS) * 1000
    }

    #[test]
    fn liest_verlauf_top_sites_und_laesst_nicht_web_seiten_weg() {
        let t = testordner("chromium-verlauf");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        {
            let conn = Connection::open(profil.join("History")).unwrap();
            let alt = webkit(1_000_000);
            let neu = webkit(2_000_000);
            conn.execute_batch(&format!(
                "CREATE TABLE urls (url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER, hidden INTEGER);
                 CREATE TABLE top_sites (url TEXT, url_rank INTEGER, title TEXT);
                 INSERT INTO urls VALUES ('https://alt.example/', 'Alt', 2, {alt}, 0);
                 INSERT INTO urls VALUES ('https://neu.example/', 'Neu', 7, {neu}, 0);
                 INSERT INTO urls VALUES ('chrome://settings', 'Einstellungen', 50, {neu}, 0);
                 INSERT INTO urls VALUES ('https://versteckt.example/', 'Versteckt', 9, {neu}, 1);
                 INSERT INTO urls VALUES ('https://javascript.example/', 'JS', 3, {alt}, 0);
                 INSERT INTO top_sites VALUES ('https://top.example/', 1, 'Top');"
            ))
            .unwrap();
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        assert!(schatten.kopiere(&profil.join("History"), &profil, "History", 1024 * 1024).unwrap());

        let gelesen = lesen(&schatten);
        assert_eq!(gelesen.verlauf.iter().map(|e| e.url.as_str()).collect::<Vec<_>>(), [
            "https://neu.example/",
            "https://alt.example/",
            "https://javascript.example/"
        ]);
        assert_eq!(gelesen.top_sites[0].url, "https://top.example/");
        assert_eq!(gelesen.verlauf[0].anzahl, 7);
        assert!(gelesen.verlauf[0].zeit > gelesen.verlauf[1].zeit);
    }

    #[test]
    fn faellt_ohne_top_sites_auf_den_meistbesuchten_verlauf_zurueck() {
        let t = testordner("chromium-top-fallback");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        {
            let conn = Connection::open(profil.join("History")).unwrap();
            let a = webkit(1_000);
            let b = webkit(2_000);
            conn.execute_batch(&format!(
                "CREATE TABLE urls (url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);
                 INSERT INTO urls VALUES ('https://a.example/', 'A', 1, {a});
                 INSERT INTO urls VALUES ('https://b.example/', 'B', 9, {b});"
            ))
            .unwrap();
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("History"), &profil, "History", 1024 * 1024).unwrap();
        let gelesen = lesen(&schatten);
        assert_eq!(gelesen.top_sites.iter().map(|t| t.url.as_str()).collect::<Vec<_>>(), [
            "https://b.example/",
            "https://a.example/"
        ]);
    }

    #[test]
    fn liest_lesezeichen_mit_ordnern_und_ueberspringt_schlechte_adressen() {
        let t = testordner("chromium-lesezeichen");
        let bookmarks = serde_json::json!({
            "roots": {
                "bookmark_bar": {
                    "type": "folder", "name": "Leiste", "children": [
                        {"type": "url", "name": "Beispiel", "url": "https://beispiel.example/", "date_added": "13200000000000000"},
                        {"type": "url", "name": "Doppelt", "url": "https://beispiel.example/", "date_added": "0"},
                        {"type": "url", "name": "Böse", "url": "javascript:alert(1)"},
                        {"type": "folder", "name": "Dev", "children": [
                            {"type": "url", "name": "Rust", "url": "https://rust-lang.org/", "date_added": "0"}
                        ]}
                    ]
                },
                "other": {"type": "folder", "name": "Andere", "children": [
                    {"type": "url", "name": "Gleiche Adresse anderswo", "url": "https://beispiel.example/"}
                ]}
            }
        });
        let datei_pfad = t.0.join("Bookmarks");
        datei(&datei_pfad, serde_json::to_string(&bookmarks).unwrap().as_bytes());

        let mut warnungen = Vec::new();
        let eintraege = lesezeichen(&datei_pfad, &mut warnungen);
        assert_eq!(eintraege.len(), 3, "gleiche Adresse im selben Ordner nur einmal");
        assert_eq!(eintraege[0].ordner, ["Lesezeichenleiste"]);
        assert_eq!(eintraege[1].ordner, ["Lesezeichenleiste", "Dev"]);
        assert_eq!(eintraege[2].ordner, ["Weitere Lesezeichen"]);
        assert_eq!(eintraege[0].titel, "Beispiel");
        assert!(eintraege[0].zeit > 0);
        assert!(warnungen.is_empty());
    }

    #[test]
    fn kappt_lesezeichen_und_ordnungstiefe() {
        let mut kinder = vec![serde_json::json!({"type": "url", "name": "Zu tief", "url": "https://tief.example/"})];
        for _ in 0..30 {
            kinder = vec![serde_json::json!({"type": "folder", "name": "Ebene", "children": kinder})];
        }
        let mut leiste = Vec::new();
        for i in 0..(LESEBZEICHEN_MAX + 3) {
            leiste.push(serde_json::json!({"type": "url", "name": format!("Seite {i}"), "url": format!("https://s{i}.example/")}));
        }
        leiste.push(serde_json::json!({"type": "folder", "name": "Tief", "children": kinder}));
        let bookmarks = serde_json::json!({"roots": {"bookmark_bar": {"type": "folder", "children": leiste}}});
        let t = testordner("chromium-grenzen");
        let datei_pfad = t.0.join("Bookmarks");
        datei(&datei_pfad, serde_json::to_string(&bookmarks).unwrap().as_bytes());

        let mut warnungen = Vec::new();
        let eintraege = lesezeichen(&datei_pfad, &mut warnungen);
        assert_eq!(eintraege.len(), LESEBZEICHEN_MAX);
        assert!(warnungen.iter().any(|w| w.code == "lesezeichen_gekuerzt"));
        assert!(!eintraege.iter().any(|e| e.url == "https://tief.example/"));
    }

    #[test]
    fn kappt_den_verlauf_auf_5000_und_meldet_es() {
        let t = testordner("chromium-verlauf-grenze");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        {
            let mut conn = Connection::open(profil.join("History")).unwrap();
            conn.execute_batch("CREATE TABLE urls (url TEXT, title TEXT, visit_count INTEGER, last_visit_time INTEGER);")
                .unwrap();
            let tx = conn.transaction().unwrap();
            for i in 0..(VERLAUF_MAX + 5) {
                tx.execute(
                    "INSERT INTO urls VALUES (?1, ?2, 1, ?3)",
                    rusqlite::params![format!("https://s{i}.example/"), format!("Seite {i}"), webkit(1_000 + i as i64)],
                )
                .unwrap();
            }
            tx.commit().unwrap();
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("History"), &profil, "History", 1024 * 1024).unwrap();
        let gelesen = lesen(&schatten);
        assert_eq!(gelesen.verlauf.len(), VERLAUF_MAX);
        assert!(gelesen.warnungen.iter().any(|w| w.code == "verlauf_gekuerzt"));
    }

    #[test]
    fn waehlt_das_groesste_symbol_je_herkunft() {
        let t = testordner("chromium-symbole");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        {
            let conn = Connection::open(profil.join("Favicons")).unwrap();
            conn.execute_batch(
                "CREATE TABLE icon_mapping (page_url TEXT, icon_id INTEGER);
                 CREATE TABLE favicon_bitmaps (icon_id INTEGER, width INTEGER, height INTEGER, image_data BLOB);
                 INSERT INTO icon_mapping VALUES ('https://a.example/seite', 4);
                 INSERT INTO icon_mapping VALUES ('https://fremd.example/', 9);
                 INSERT INTO favicon_bitmaps VALUES (4, 16, 16, X'01');
                 INSERT INTO favicon_bitmaps VALUES (4, 32, 32, X'02');
                 INSERT INTO favicon_bitmaps VALUES (9, 64, 64, X'03');",
            )
            .unwrap();
        }
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("Favicons"), &profil, "Favicons", 1024 * 1024).unwrap();
        let conn = oeffnen(&schatten.pfad("Favicons")).unwrap();
        let mut warnungen = Vec::new();
        let wanted = vec!["https://a.example".to_string()];
        let bilder = roh_bilder(Some(&conn), &wanted, &mut warnungen);
        assert_eq!(bilder.len(), 1);
        assert_eq!(bilder[0].herkunft, "https://a.example");
        assert_eq!(bilder[0].kandidaten[0], vec![2]);
        assert_eq!(bilder[0].kandidaten[1], vec![1]);
    }

    #[test]
    fn fehlendes_schema_ist_eine_warnung_und_kein_absturz() {
        let t = testordner("chromium-schema");
        let profil = t.0.join("Profil");
        fs::create_dir_all(&profil).unwrap();
        Connection::open(profil.join("History")).unwrap().execute_batch("CREATE TABLE fremd (x);").unwrap();
        let schatten = Schatten::neu_in(&t.0.join("schatten")).unwrap();
        schatten.kopiere(&profil.join("History"), &profil, "History", 1024 * 1024).unwrap();
        let gelesen = lesen(&schatten);
        assert!(gelesen.verlauf.is_empty());
        assert!(gelesen.warnungen.iter().any(|w| w.code == "verlauf_kaputt"));
    }
}
