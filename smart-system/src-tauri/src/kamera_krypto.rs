//! Verschlüsselung der Kamera-Sicherung, wenn die App nicht offen ist.
//!
//! Der Hintergrund-Job (`KameraArbeit.kt`) hat keinen WebView und damit kein
//! DIS. Hier steht die verschlüsselnde Hälfte nach, Byte für Byte wie im
//! WebView:
//!
//! - `hybrid_verschluesseln` ist `hybridEncrypt` aus `@msdis/shield/post-quantum`
//!   im Format v4: ML-KEM-768 und RSA-4096-OAEP-SHA-256, beide Geheimnisse per
//!   HKDF-SHA-256 zu einem AES-256-GCM-Schlüssel verbunden.
//! - `chunk_verschluesseln` ist `chunkVerschluesseln` aus `tresorDatei.ts`:
//!   Polsterung auf die Größenklasse, AAD aus DIS `chunkAad` mit dem
//!   Manifest-Root aus `computeManifestRoot`.
//!
//! Entschlüsseln kann dieses Modul nicht. Das Telefon soll bei gesperrtem
//! Tresor sichern, aber nichts lesen können; den privaten Schlüssel des
//! Posteingangs kennt es nicht.
//!
//! Dass beides zu DIS passt, prüfen Vektoren in beide Richtungen
//! (`frontend/src/desktop/vault/__vektoren__/`): die Tests hier öffnen, was DIS
//! verschlüsselt hat, und `kameraVektoren.test.ts` öffnet mit DIS, was dieses
//! Modul verschlüsselt hat.
//!
//! Alles kommt aus `aws-lc-rs`, das über rustls ohnehin im Baum liegt.
//!
//! Schlüssel und Klartext liegen in `Zeroizing`: sie werden beim Verlassen
//! jedes Weges genullt, auch bei einem frühen Fehler. Ein `fill(0)` von Hand
//! verpasst die Fehlerwege, und der Compiler darf es als tote Schreibarbeit
//! streichen.

// Außer auf Android ruft nur die Prüfung in `medien.rs` hinein; Chunks verschlüsselt allein der Job.
#![cfg_attr(not(target_os = "android"), allow(dead_code))]

use aws_lc_rs::aead::{Aad, LessSafeKey, Nonce, UnboundKey, AES_256_GCM};
use aws_lc_rs::digest::{digest, SHA256};
use aws_lc_rs::hkdf::{Salt, HKDF_SHA256};
use aws_lc_rs::kem::{EncapsulationKey, ML_KEM_768};
use aws_lc_rs::rand::{SecureRandom, SystemRandom};
use aws_lc_rs::rsa::{OaepPublicEncryptingKey, PublicEncryptingKey, OAEP_SHA256_MGF1SHA256};
use base64::Engine;
use zeroize::Zeroizing;

/// Klartext je Chunk, `DEFAULT_CHUNK_SIZE` in DIS.
pub const CHUNK: u64 = 4 * 1024 * 1024;
const IV: usize = 12;
const TAG: usize = 16;
const KLEINSTE_KLASSE: u64 = 64 * 1024;
pub const MINIATUR: u64 = 32 * 1024;
pub const VORSCHAU: u64 = 512 * 1024;
/// `TRESOR_BLOB_BESITZER` in `tresorDatei.ts`.
const BESITZER: &str = "msm-tresor-v1";
const DATEI_REVISION: u32 = 1;

const HYBRID_V2: u8 = 0x04;
const ML_KEM_768_OEFFENTLICH: usize = 1184;
const ML_KEM_768_KAPSEL: usize = 1088;
const RSA_4096: usize = 512;
const HYBRID_INFO: &[u8] = b"Singra Vault-HybridKDF-v2:";

fn zufall(laenge: usize) -> Result<Vec<u8>, String> {
    let mut wert = vec![0u8; laenge];
    SystemRandom::new().fill(&mut wert).map_err(|_| "Kein Zufall verfügbar".to_string())?;
    Ok(wert)
}

/// AES-256-GCM, Ergebnis Chiffrat ‖ Tag. Der Puffer hat vorab Platz für den
/// Tag: wüchse er beim Anhängen, bliebe die alte Kopie des Klartexts ungenullt
/// im freigegebenen Speicher.
fn gcm(schluessel: &[u8], iv: &[u8], aad: &[u8], klartext: &[u8]) -> Result<Vec<u8>, String> {
    let key = UnboundKey::new(&AES_256_GCM, schluessel).map_err(|_| "Ungültiger Schlüssel".to_string())?;
    let nonce = Nonce::try_assume_unique_for_key(iv).map_err(|_| "Ungültiger IV".to_string())?;
    let mut daten = Zeroizing::new(Vec::with_capacity(klartext.len() + TAG));
    daten.extend_from_slice(klartext);
    LessSafeKey::new(key)
        .seal_in_place_append_tag(nonce, Aad::from(aad), &mut *daten)
        .map_err(|_| "Verschlüsseln gescheitert".to_string())?;
    // Jetzt Chiffrat; was zurückbleibt, ist leer.
    Ok(std::mem::take(&mut *daten))
}

/// HKDF-v2 aus DIS: IKM = ML-KEM-Geheimnis ‖ AES-Schlüssel, Salz 32 Nullbytes,
/// Info = Präfix ‖ RSA-Chiffrat.
fn hybrid_schluessel(pq_geheimnis: &[u8], aes: &[u8], rsa_chiffrat: &[u8]) -> Result<Zeroizing<[u8; 32]>, String> {
    let mut ikm = Zeroizing::new(Vec::with_capacity(pq_geheimnis.len() + aes.len()));
    ikm.extend_from_slice(pq_geheimnis);
    ikm.extend_from_slice(aes);
    let mut info = HYBRID_INFO.to_vec();
    info.extend_from_slice(rsa_chiffrat);
    let mut schluessel = Zeroizing::new([0u8; 32]);
    let prk = Salt::new(HKDF_SHA256, &[0u8; 32]).extract(&ikm);
    prk.expand(&[&info], HKDF_SHA256)
        .and_then(|okm| okm.fill(&mut *schluessel))
        .map_err(|_| "HKDF gescheitert".to_string())?;
    Ok(schluessel)
}

/// `hybridEncrypt(klartext, pq, rsa, aad)` aus DIS, als Bytes statt Base64.
/// `rsa_spki` ist der öffentliche RSA-Schlüssel als SubjectPublicKeyInfo (DER).
pub fn hybrid_verschluesseln(klartext: &[u8], pq_oeffentlich: &[u8], rsa_spki: &[u8], aad: &[u8]) -> Result<Vec<u8>, String> {
    if pq_oeffentlich.len() != ML_KEM_768_OEFFENTLICH {
        return Err("Ungültiger ML-KEM-Schlüssel".into());
    }
    if aad.is_empty() {
        return Err("Ohne AAD wird nicht verschlüsselt".into());
    }
    let ek = EncapsulationKey::new(&ML_KEM_768, pq_oeffentlich).map_err(|_| "Ungültiger ML-KEM-Schlüssel".to_string())?;
    let (kapsel, pq_geheimnis) = ek.encapsulate().map_err(|_| "ML-KEM gescheitert".to_string())?;

    let rsa = PublicEncryptingKey::from_der(rsa_spki)
        .ok()
        .and_then(|k| OaepPublicEncryptingKey::new(k).ok())
        .ok_or_else(|| "Ungültiger RSA-Schlüssel".to_string())?;
    if rsa.ciphertext_size() != RSA_4096 {
        return Err("Der RSA-Schlüssel hat nicht 4096 Bit".into());
    }
    let aes = Zeroizing::new(zufall(32)?);
    let mut rsa_chiffrat = vec![0u8; RSA_4096];
    let laenge = rsa
        .encrypt(&OAEP_SHA256_MGF1SHA256, &aes, &mut rsa_chiffrat, None)
        .map_err(|_| "RSA gescheitert".to_string())?
        .len();
    rsa_chiffrat.truncate(laenge);

    let schluessel = hybrid_schluessel(pq_geheimnis.as_ref(), &aes, &rsa_chiffrat)?;
    let iv = zufall(IV)?;
    let chiffrat = gcm(&*schluessel, &iv, aad, klartext)?;

    let mut aus = Vec::with_capacity(1 + ML_KEM_768_KAPSEL + RSA_4096 + IV + chiffrat.len());
    aus.push(HYBRID_V2);
    aus.extend_from_slice(kapsel.as_ref());
    aus.extend_from_slice(&rsa_chiffrat);
    aus.extend_from_slice(&iv);
    aus.extend_from_slice(&chiffrat);
    Ok(aus)
}

/// Größenklasse eines Blobs, wie `gepolsterteGroesse` in `tresorDatei.ts`:
/// unter 4 MiB die nächste Zweierpotenz (mindestens 64 KiB), darüber Padmé.
pub fn gepolsterte_groesse(echt: u64) -> u64 {
    if echt <= KLEINSTE_KLASSE {
        return KLEINSTE_KLASSE;
    }
    if echt < CHUNK {
        return echt.next_power_of_two();
    }
    let e = 63 - echt.leading_zeros() as u64;
    let s = (63 - e.leading_zeros() as u64) + 1;
    let schritt = 1u64 << (e - s);
    echt.div_ceil(schritt) * schritt
}

/// `groessePasst` in `tresorDatei.ts`.
pub fn groesse_passt(groesse: u64, echt: u64) -> bool {
    echt <= groesse && (groesse == gepolsterte_groesse(echt) || groesse == MINIATUR || groesse == VORSCHAU)
}

pub fn chunk_anzahl(groesse: u64) -> u64 {
    groesse.div_ceil(CHUNK).max(1)
}

/// Klartextlänge von Chunk `index` in der gepolsterten Datei.
pub fn chunk_laenge(groesse: u64, index: u64) -> u64 {
    CHUNK.min(groesse - index * CHUNK)
}

/// `computeManifestRoot` aus DIS: SHA-256 (Base64) über das JSON der Aufteilung.
/// `storage_path: undefined` lässt `JSON.stringify` weg.
pub fn manifest_wurzel(blob_id: &str, groesse: u64) -> String {
    let anzahl = chunk_anzahl(groesse);
    let chunks: Vec<String> = (0..anzahl)
        .map(|i| format!("{{\"index\":{i},\"plaintext_size\":{}}}", chunk_laenge(groesse, i)))
        .collect();
    let json = format!(
        "{{\"file_id\":\"{blob_id}\",\"file_revision\":{DATEI_REVISION},\"chunk_size\":{CHUNK},\"chunk_count\":{anzahl},\"chunks\":[{}]}}",
        chunks.join(",")
    );
    base64::engine::general_purpose::STANDARD.encode(digest(&SHA256, json.as_bytes()).as_ref())
}

/// `chunkAad` aus DIS mit dem Kontext eines Tresor-Blobs.
pub fn chunk_aad(eintrag_id: &str, blob_id: &str, groesse: u64, index: u64) -> String {
    format!(
        "sv-file-chunk-v1:{BESITZER}:{eintrag_id}:{blob_id}:{DATEI_REVISION}:{}:{index}:{}",
        manifest_wurzel(blob_id, groesse),
        chunk_anzahl(groesse)
    )
}

fn ist_hex(wert: &str, laenge: usize) -> bool {
    wert.len() == laenge && wert.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn ist_uuid(wert: &str) -> bool {
    let teile: Vec<&str> = wert.split('-').collect();
    teile.len() == 5 && teile.iter().zip([8, 4, 4, 4, 12]).all(|(t, l)| ist_hex(t, l))
}

/// Wie viele echte Bytes Chunk `index` trägt; der Rest ist Polsterung.
pub fn echte_bytes(groesse: u64, echt: u64, index: u64) -> u64 {
    chunk_laenge(groesse, index).min(echt.saturating_sub(index * CHUNK))
}

/// `chunkVerschluesseln` aus `tresorDatei.ts`: IV ‖ Chiffrat ‖ Tag, Klartext
/// mit Nullen bis zur gepolsterten Länge. `daten` sind die echten Bytes dieses
/// Abschnitts.
pub fn chunk_verschluesseln(
    schluessel: &[u8],
    daten: &[u8],
    groesse: u64,
    echt: u64,
    index: u64,
    eintrag_id: &str,
    blob_id: &str,
) -> Result<Vec<u8>, String> {
    if schluessel.len() != 32 {
        return Err("Ungültiger Schlüssel".into());
    }
    if !ist_uuid(eintrag_id) || !ist_hex(blob_id, 32) {
        return Err("Ungültige Kennung".into());
    }
    if !groesse_passt(groesse, echt) {
        return Err("Ungültige Blob-Größe".into());
    }
    if index >= chunk_anzahl(groesse) {
        return Err("Chunk außerhalb des Blobs".into());
    }
    if daten.len() as u64 != echte_bytes(groesse, echt, index) {
        return Err("Chunk hat die falsche Länge".into());
    }
    let mut klartext = Zeroizing::new(vec![0u8; chunk_laenge(groesse, index) as usize]);
    klartext[..daten.len()].copy_from_slice(daten);
    let iv = zufall(IV)?;
    let chiffrat = gcm(schluessel, &iv, chunk_aad(eintrag_id, blob_id, groesse, index).as_bytes(), &klartext)?;
    let mut aus = Vec::with_capacity(IV + chiffrat.len());
    aus.extend_from_slice(&iv);
    aus.extend_from_slice(&chiffrat);
    debug_assert_eq!(aus.len() as u64, chunk_laenge(groesse, index) + (IV + TAG) as u64);
    Ok(aus)
}

/// Aufrufe aus `KameraKrypto.kt`. Bytes kommen und gehen als `ByteArray`, ein
/// Fehler wird zur `IllegalArgumentException`.
#[cfg(target_os = "android")]
mod jni_bruecke {
    use jni::objects::{JByteArray, JClass, JString};
    use jni::sys::{jbyteArray, jint, jlong};
    use jni::JNIEnv;
    use zeroize::Zeroizing;

    fn werfen(env: &mut JNIEnv, text: &str) -> jbyteArray {
        let _ = env.throw_new("java/lang/IllegalArgumentException", text);
        std::ptr::null_mut()
    }

    fn bytes(env: &mut JNIEnv, wert: &JByteArray) -> Result<Vec<u8>, String> {
        env.convert_byte_array(wert).map_err(|_| "Bytes nicht lesbar".to_string())
    }

    /// Wie `bytes`, für Schlüssel und Klartext: genullt, sobald der Aufruf endet, auf jedem Weg.
    fn geheim(env: &mut JNIEnv, wert: &JByteArray) -> Result<Zeroizing<Vec<u8>>, String> {
        bytes(env, wert).map(Zeroizing::new)
    }

    fn text(env: &mut JNIEnv, wert: &JString) -> Result<String, String> {
        env.get_string(wert).map(String::from).map_err(|_| "Text nicht lesbar".to_string())
    }

    fn zurueck(env: &mut JNIEnv, ergebnis: Result<Vec<u8>, String>) -> jbyteArray {
        match ergebnis.and_then(|b| env.byte_array_from_slice(&b).map_err(|_| "Kein Speicher".to_string())) {
            Ok(feld) => feld.into_raw(),
            Err(fehler) => werfen(env, &fehler),
        }
    }

    #[no_mangle]
    pub extern "system" fn Java_com_mauntingstudios_smart_1system_KameraKrypto_hybrid<'l>(
        mut env: JNIEnv<'l>,
        _: JClass<'l>,
        klartext: JByteArray<'l>,
        pq: JByteArray<'l>,
        rsa_spki: JByteArray<'l>,
        aad: JByteArray<'l>,
    ) -> jbyteArray {
        let ergebnis = (|| {
            let klar = geheim(&mut env, &klartext)?;
            super::hybrid_verschluesseln(&klar, &bytes(&mut env, &pq)?, &bytes(&mut env, &rsa_spki)?, &bytes(&mut env, &aad)?)
        })();
        zurueck(&mut env, ergebnis)
    }

    #[no_mangle]
    #[allow(clippy::too_many_arguments)]
    pub extern "system" fn Java_com_mauntingstudios_smart_1system_KameraKrypto_chunk<'l>(
        mut env: JNIEnv<'l>,
        _: JClass<'l>,
        schluessel: JByteArray<'l>,
        daten: JByteArray<'l>,
        groesse: jlong,
        echt: jlong,
        index: jint,
        eintrag_id: JString<'l>,
        blob_id: JString<'l>,
    ) -> jbyteArray {
        let ergebnis = (|| {
            if groesse < 0 || echt < 0 || index < 0 {
                return Err("Negative Größe".to_string());
            }
            let schl = geheim(&mut env, &schluessel)?;
            let klar = geheim(&mut env, &daten)?;
            let eintrag = text(&mut env, &eintrag_id)?;
            let blob = text(&mut env, &blob_id)?;
            super::chunk_verschluesseln(&schl, &klar, groesse as u64, echt as u64, index as u64, &eintrag, &blob)
        })();
        zurueck(&mut env, ergebnis)
    }

    #[no_mangle]
    pub extern "system" fn Java_com_mauntingstudios_smart_1system_KameraKrypto_gepolstert<'l>(
        _env: JNIEnv<'l>,
        _: JClass<'l>,
        echt: jlong,
    ) -> jlong {
        if echt < 0 {
            return -1;
        }
        super::gepolsterte_groesse(echt as u64) as jlong
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use aws_lc_rs::kem::DecapsulationKey;
    use aws_lc_rs::rsa::{OaepPrivateDecryptingKey, PrivateDecryptingKey};
    use serde_json::Value;

    const TS: &str = include_str!("../../../frontend/src/desktop/vault/__vektoren__/kamera_ts.json");
    const RUST_DATEI: &str = "../../frontend/src/desktop/vault/__vektoren__/kamera_rust.json";

    fn b64(wert: &Value) -> Vec<u8> {
        base64::engine::general_purpose::STANDARD.decode(wert.as_str().unwrap()).unwrap()
    }

    fn hex(wert: &str) -> Vec<u8> {
        (0..wert.len()).step_by(2).map(|i| u8::from_str_radix(&wert[i..i + 2], 16).unwrap()).collect()
    }

    fn vektoren() -> Value {
        serde_json::from_str(TS).unwrap()
    }

    /// Spiegel von `hybridDecrypt` (DIS), nur für die Tests.
    fn hybrid_oeffnen(chiffrat: &[u8], pq_geheim: &[u8], rsa_pkcs8: &[u8], aad: &[u8]) -> Vec<u8> {
        assert_eq!(chiffrat[0], HYBRID_V2);
        let (kapsel, rest) = chiffrat[1..].split_at(ML_KEM_768_KAPSEL);
        let (rsa_chiffrat, rest) = rest.split_at(RSA_4096);
        let (iv, aes_chiffrat) = rest.split_at(IV);
        let dk = DecapsulationKey::new(&ML_KEM_768, pq_geheim).unwrap();
        let pq_geheimnis = dk.decapsulate(kapsel.into()).unwrap();
        let rsa = OaepPrivateDecryptingKey::new(PrivateDecryptingKey::from_pkcs8(rsa_pkcs8).unwrap()).unwrap();
        let mut puffer = vec![0u8; rsa.min_output_size()];
        let aes = rsa.decrypt(&OAEP_SHA256_MGF1SHA256, rsa_chiffrat, &mut puffer, None).unwrap().to_vec();
        let schluessel = hybrid_schluessel(pq_geheimnis.as_ref(), &aes, rsa_chiffrat).unwrap();
        oeffnen(&*schluessel, iv, aad, aes_chiffrat)
    }

    fn oeffnen(schluessel: &[u8], iv: &[u8], aad: &[u8], chiffrat: &[u8]) -> Vec<u8> {
        let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, schluessel).unwrap());
        let mut daten = chiffrat.to_vec();
        let n = key
            .open_in_place(Nonce::try_assume_unique_for_key(iv).unwrap(), Aad::from(aad), &mut daten)
            .unwrap()
            .len();
        daten.truncate(n);
        daten
    }

    /// Spiegel von `chunkEntschluesseln` (`tresorDatei.ts`): nur die echten Bytes.
    fn chunk_oeffnen(chiffrat: &[u8], schluessel: &[u8], groesse: u64, echt: u64, index: u64, eintrag: &str, blob: &str) -> Vec<u8> {
        assert_eq!(chiffrat.len() as u64, chunk_laenge(groesse, index) + (IV + TAG) as u64);
        let mut klar = oeffnen(schluessel, &chiffrat[..IV], chunk_aad(eintrag, blob, groesse, index).as_bytes(), &chiffrat[IV..]);
        klar.truncate(echte_bytes(groesse, echt, index) as usize);
        klar
    }

    fn muster(laenge: u64) -> Vec<u8> {
        (0..laenge).map(|i| (i % 251) as u8).collect()
    }

    #[test]
    fn groessen_wie_im_webview() {
        for paar in vektoren()["groessen"].as_array().unwrap() {
            let (echt, groesse) = (paar[0].as_u64().unwrap(), paar[1].as_u64().unwrap());
            assert_eq!(gepolsterte_groesse(echt), groesse, "echt = {echt}");
        }
    }

    #[test]
    fn manifest_und_aad_wie_dis() {
        let v = &vektoren()["gross"];
        let (blob, groesse) = (v["id"].as_str().unwrap(), v["groesse"].as_u64().unwrap());
        assert_eq!(manifest_wurzel(blob, groesse), v["wurzel"].as_str().unwrap());
        assert_eq!(chunk_aad(v["eintragId"].as_str().unwrap(), blob, groesse, 1), v["aad1"].as_str().unwrap());
    }

    #[test]
    fn oeffnet_was_dis_verschluesselt_hat() {
        let v = vektoren();
        let h = &v["hybrid"];
        let klar = hybrid_oeffnen(&b64(&h["chiffrat"]), &b64(&v["pqSecretKey"]), &b64(&v["rsaPkcs8"]), h["aad"].as_str().unwrap().as_bytes());
        assert_eq!(String::from_utf8(klar).unwrap(), h["klartext"].as_str().unwrap());

        let b = &v["blob"];
        let (groesse, echt) = (b["groesse"].as_u64().unwrap(), b["echt"].as_u64().unwrap());
        let klar = chunk_oeffnen(
            &b64(&b["chunk0"]),
            &hex(b["schluessel"].as_str().unwrap()),
            groesse,
            echt,
            0,
            b["eintragId"].as_str().unwrap(),
            b["id"].as_str().unwrap(),
        );
        assert_eq!(klar, muster(echt));
    }

    #[test]
    fn verschluesselt_so_wie_dis_es_oeffnet() {
        let v = vektoren();
        let klartext = "Kamera-Sicherung \u{2013} Probe";
        let aad = "msm-tresor-eingang-v1:probe";
        let chiffrat = hybrid_verschluesseln(klartext.as_bytes(), &b64(&v["pqPublicKey"]), &b64(&v["rsaSpki"]), aad.as_bytes()).unwrap();
        assert_eq!(hybrid_oeffnen(&chiffrat, &b64(&v["pqSecretKey"]), &b64(&v["rsaPkcs8"]), aad.as_bytes()), klartext.as_bytes());

        let g = &v["gross"];
        let (groesse, echt) = (g["groesse"].as_u64().unwrap(), g["echt"].as_u64().unwrap());
        let schluessel = hex(g["schluessel"].as_str().unwrap());
        let (eintrag, blob) = (g["eintragId"].as_str().unwrap(), g["id"].as_str().unwrap());
        let daten = muster(echt - CHUNK);
        let chunk = chunk_verschluesseln(&schluessel, &daten, groesse, echt, 1, eintrag, blob).unwrap();
        assert_eq!(chunk_oeffnen(&chunk, &schluessel, groesse, echt, 1, eintrag, blob), daten);
    }

    #[test]
    fn lehnt_ab_was_nicht_passt() {
        let v = vektoren();
        let (pq, rsa) = (b64(&v["pqPublicKey"]), b64(&v["rsaSpki"]));
        assert!(hybrid_verschluesseln(b"x", &pq[1..], &rsa, b"aad").is_err());
        assert!(hybrid_verschluesseln(b"x", &pq, &rsa, b"").is_err());
        assert!(hybrid_verschluesseln(b"x", &pq, &rsa[..100], b"aad").is_err());

        let id = "0123456789abcdef0123456789abcdef";
        let eintrag = "0f8fad5b-d9cb-469f-a165-70867728950e";
        let k = [7u8; 32];
        // Falsche Länge, falscher Index, keine Größenklasse, keine Kennung.
        assert!(chunk_verschluesseln(&k, &[1, 2], KLEINSTE_KLASSE, 3, 0, eintrag, id).is_err());
        assert!(chunk_verschluesseln(&k, &[], KLEINSTE_KLASSE, 0, 1, eintrag, id).is_err());
        assert!(chunk_verschluesseln(&k, &[1], 70_000, 1, 0, eintrag, id).is_err());
        assert!(chunk_verschluesseln(&k, &[1], KLEINSTE_KLASSE, 1, 0, "../x", id).is_err());
        assert!(chunk_verschluesseln(&k[..16], &[1], KLEINSTE_KLASSE, 1, 0, eintrag, id).is_err());
        // Miniatur und Vorschau sind eigene Klassen, auch leer.
        assert!(chunk_verschluesseln(&k, &[], MINIATUR, 0, 0, eintrag, id).is_ok());
        assert!(chunk_verschluesseln(&k, &[], VORSCHAU, 0, 0, eintrag, id).is_ok());
    }

    /// Schreibt die Gegenrichtung für `kameraVektoren.test.ts`. Nur auf Wunsch:
    /// `KAMERA_VEKTOREN_SCHREIBEN=1 cargo test kamera_krypto`.
    #[test]
    fn schreibt_vektoren_fuer_dis() {
        if std::env::var("KAMERA_VEKTOREN_SCHREIBEN").is_err() {
            return;
        }
        let v = vektoren();
        let b64e = |b: &[u8]| base64::engine::general_purpose::STANDARD.encode(b);
        let klartext = "{\"name\":\"IMG_0001.jpg\",\"typ\":\"image/jpeg\"} \u{e4}\u{f6}\u{fc}";
        let aad = "msm-tresor-eingang-v1:rust";
        let hybrid = hybrid_verschluesseln(klartext.as_bytes(), &b64(&v["pqPublicKey"]), &b64(&v["rsaSpki"]), aad.as_bytes()).unwrap();

        let b = &v["blob"];
        let (groesse, echt) = (b["groesse"].as_u64().unwrap(), b["echt"].as_u64().unwrap());
        let chunk0 = chunk_verschluesseln(
            &hex(b["schluessel"].as_str().unwrap()),
            &muster(echt),
            groesse,
            echt,
            0,
            b["eintragId"].as_str().unwrap(),
            b["id"].as_str().unwrap(),
        )
        .unwrap();
        let g = &v["gross"];
        let (ggroesse, gecht) = (g["groesse"].as_u64().unwrap(), g["echt"].as_u64().unwrap());
        let chunk1 = chunk_verschluesseln(
            &hex(g["schluessel"].as_str().unwrap()),
            &muster(gecht - CHUNK),
            ggroesse,
            gecht,
            1,
            g["eintragId"].as_str().unwrap(),
            g["id"].as_str().unwrap(),
        )
        .unwrap();
        let aus = serde_json::json!({
            "hybrid": { "klartext": klartext, "aad": aad, "chiffrat": b64e(&hybrid) },
            "chunk0": b64e(&chunk0),
            "chunk1": b64e(&chunk1),
        });
        std::fs::write(RUST_DATEI, serde_json::to_string_pretty(&aus).unwrap() + "\n").unwrap();
    }
}
