//! Ein CRX3-Paket lesen und prüfen, wie Chromium es tut
//! (`components/crx_file/crx_verifier.cc`, `crx3.proto`).
//!
//! Aufbau: `Cr24`, Version 3, Länge des Kopfs, Kopf (Protobuf), ZIP. Der Kopf
//! trägt Beweise (öffentlicher Schlüssel und Signatur, RSA oder ECDSA) und die
//! signierten Daten mit der Kennung. Jede Signatur deckt
//! `"CRX3 SignedData\0"`, die Länge der signierten Daten, diese selbst und das
//! ganze ZIP. Gültig ist ein Paket nur, wenn jeder Beweis stimmt, einer davon
//! zur Kennung gehört (der Entwicklerschlüssel) und einer vom Web Store stammt.

use ring::digest::{digest, SHA256};

const MAGIC: &[u8; 4] = b"Cr24";
const KONTEXT: &[u8] = b"CRX3 SignedData\0";

/// SHA-256 des Schlüssels, mit dem der Chrome Web Store jedes Paket
/// unterschreibt (`kPublisherKeyHash` in `crx_verifier.cc`).
pub const STORE_SCHLUESSEL: [u8; 32] = [
    0x61, 0xf7, 0xf2, 0xa6, 0xbf, 0xcf, 0x74, 0xcd, 0x0b, 0xc1, 0xfe, 0x24, 0x97, 0xcc, 0x9b, 0x04, 0x25, 0x4c, 0x65, 0x8f, 0x79, 0xf2, 0x14,
    0x53, 0x92, 0x86, 0x7e, 0xa8, 0x36, 0x63, 0x67, 0xcf,
];

/// Fehlercodes für die Oberfläche (`browser.erweiterungen.fehler.*`).
#[derive(Debug, PartialEq)]
pub enum Fehler {
    /// Kein CRX3, kaputter Kopf, ZIP-Marken im Kopf.
    Ungueltig,
    /// Eine Signatur stimmt nicht: das Paket wurde verändert.
    Signatur,
    /// Das Paket gehört zu einer anderen Erweiterung.
    FalscheId,
    /// Ohne Unterschrift des Web Store.
    NichtAusDemStore,
}

impl Fehler {
    pub fn code(&self) -> &'static str {
        match self {
            Fehler::Ungueltig => "ungueltig",
            Fehler::Signatur => "signatur",
            Fehler::FalscheId => "falsche_id",
            Fehler::NichtAusDemStore => "nicht_aus_dem_store",
        }
    }
}

/// Ein geprüftes Paket.
#[derive(Debug)]
pub struct Paket<'a> {
    /// 32 Buchstaben a–p, wie Chromium sie schreibt.
    pub id: String,
    /// Der Entwicklerschlüssel (DER, SubjectPublicKeyInfo); kommt als `key`
    /// ins Manifest, damit WebView2 dieselbe Kennung vergibt.
    pub schluessel: Vec<u8>,
    pub zip: &'a [u8],
}

/// Kennung aus den ersten 16 Bytes eines SHA-256: jede Hälfte eines Bytes
/// wird zu einem Buchstaben von a bis p.
pub fn id_aus(bytes: &[u8]) -> String {
    bytes.iter().take(16).flat_map(|b| [b >> 4, b & 15]).map(|n| (b'a' + n) as char).collect()
}

pub fn id_aus_schluessel(schluessel: &[u8]) -> String {
    id_aus(digest(&SHA256, schluessel).as_ref())
}

pub fn ist_id(text: &str) -> bool {
    text.len() == 32 && text.bytes().all(|b| (b'a'..=b'p').contains(&b))
}

/// Prüft `crx` für die Kennung `erwartet`. `store` ist der Hash des
/// Store-Schlüssels ([`STORE_SCHLUESSEL`]; in Tests ein eigener).
pub fn pruefen<'a>(crx: &'a [u8], erwartet: &str, store: &[u8; 32]) -> Result<Paket<'a>, Fehler> {
    if crx.len() < 12 || &crx[..4] != MAGIC || u32::from_le_bytes(crx[4..8].try_into().unwrap()) != 3 {
        return Err(Fehler::Ungueltig);
    }
    let laenge = u32::from_le_bytes(crx[8..12].try_into().unwrap()) as usize;
    let kopf = crx.get(12..12usize.checked_add(laenge).ok_or(Fehler::Ungueltig)?).ok_or(Fehler::Ungueltig)?;
    let zip = &crx[12 + laenge..];
    // Wie Chromium: eine ZIP-Endmarke im Kopf könnte das Entpacken auf
    // Daten lenken, die keine Signatur deckt.
    if [b"PK\x05\x06", b"PK\x06\x07", b"PK\x06\x06"].iter().any(|m| kopf.windows(4).any(|w| w == *m)) {
        return Err(Fehler::Ungueltig);
    }

    let mut beweise = Vec::new();
    let mut signiert: Option<&[u8]> = None;
    for (nr, wert) in felder(kopf)? {
        match nr {
            2 | 3 => beweise.push((nr == 2, beweis(wert)?)),
            10000 => signiert = Some(wert),
            _ => {}
        }
    }
    let signiert = signiert.ok_or(Fehler::Ungueltig)?;
    let crx_id = felder(signiert)?.into_iter().find(|(nr, _)| *nr == 1).map(|(_, w)| w).ok_or(Fehler::Ungueltig)?;
    if crx_id.len() != 16 {
        return Err(Fehler::Ungueltig);
    }
    let id = id_aus(crx_id);
    if id != erwartet {
        return Err(Fehler::FalscheId);
    }

    let mut nachricht = Vec::with_capacity(KONTEXT.len() + 4 + signiert.len() + zip.len());
    nachricht.extend_from_slice(KONTEXT);
    nachricht.extend_from_slice(&(signiert.len() as u32).to_le_bytes());
    nachricht.extend_from_slice(signiert);
    nachricht.extend_from_slice(zip);

    let mut entwickler = None;
    let mut vom_store = false;
    for (rsa, (schluessel, signatur)) in &beweise {
        if !signatur_stimmt(*rsa, schluessel, signatur, &nachricht) {
            return Err(Fehler::Signatur);
        }
        if id_aus_schluessel(schluessel) == id {
            entwickler = Some(schluessel.to_vec());
        }
        vom_store |= digest(&SHA256, schluessel).as_ref() == store;
    }
    let schluessel = entwickler.ok_or(Fehler::FalscheId)?;
    if !vom_store {
        return Err(Fehler::NichtAusDemStore);
    }
    Ok(Paket { id, schluessel, zip })
}

fn beweis(wert: &[u8]) -> Result<(&[u8], &[u8]), Fehler> {
    let mut schluessel = None;
    let mut signatur = None;
    for (nr, w) in felder(wert)? {
        match nr {
            1 => schluessel = Some(w),
            2 => signatur = Some(w),
            _ => {}
        }
    }
    Ok((schluessel.ok_or(Fehler::Ungueltig)?, signatur.ok_or(Fehler::Ungueltig)?))
}

fn signatur_stimmt(rsa: bool, spki: &[u8], signatur: &[u8], nachricht: &[u8]) -> bool {
    use ring::signature::{UnparsedPublicKey, ECDSA_P256_SHA256_ASN1, RSA_PKCS1_1024_8192_SHA256_FOR_LEGACY_USE_ONLY};
    let Some((rsa_schluessel, schluessel)) = spki_lesen(spki) else { return false };
    if rsa != rsa_schluessel {
        return false;
    }
    // Ältere Erweiterungen tragen noch 1024-Bit-Entwicklerschlüssel; Chromium
    // nimmt sie an. Die Unterschrift des Store prüft dieselbe Stelle.
    let ergebnis = if rsa {
        UnparsedPublicKey::new(&RSA_PKCS1_1024_8192_SHA256_FOR_LEGACY_USE_ONLY, schluessel).verify(nachricht, signatur)
    } else {
        UnparsedPublicKey::new(&ECDSA_P256_SHA256_ASN1, schluessel).verify(nachricht, signatur)
    };
    ergebnis.is_ok()
}

const RSA: &[u8] = &[0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01];
const EC: &[u8] = &[0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01];
const P256: &[u8] = &[0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07];

/// SubjectPublicKeyInfo → (ist RSA, Schlüssel in der Form, die `ring` will):
/// RSA als `RSAPublicKey`, P-256 als unkomprimierter Punkt.
fn spki_lesen(der: &[u8]) -> Option<(bool, &[u8])> {
    let (aussen, rest) = der_teil(der, 0x30)?;
    if !rest.is_empty() {
        return None;
    }
    let (algorithmus, rest) = der_teil(aussen, 0x30)?;
    let (bits, rest) = der_teil(rest, 0x03)?;
    if !rest.is_empty() || bits.first() != Some(&0) {
        return None;
    }
    let (oid, parameter) = der_teil(algorithmus, 0x06)?;
    if oid == RSA {
        return Some((true, &bits[1..]));
    }
    if oid == EC && der_teil(parameter, 0x06).is_some_and(|(kurve, _)| kurve == P256) {
        return Some((false, &bits[1..]));
    }
    None
}

/// Ein DER-Element mit dem Typ `tag`: (Inhalt, Rest).
fn der_teil(der: &[u8], tag: u8) -> Option<(&[u8], &[u8])> {
    if *der.first()? != tag {
        return None;
    }
    let (laenge, kopf): (usize, usize) = match *der.get(1)? {
        l if l < 0x80 => (l as usize, 2),
        0x81 => (*der.get(2)? as usize, 3),
        0x82 => (u16::from_be_bytes([*der.get(2)?, *der.get(3)?]) as usize, 4),
        _ => return None,
    };
    let inhalt = der.get(kopf..kopf.checked_add(laenge)?)?;
    Some((inhalt, &der[kopf + laenge..]))
}

/// Die Felder einer Protobuf-Nachricht als (Nummer, Bytes). Zahlen und feste
/// Breiten werden übersprungen; die Felder, um die es geht, sind alle Bytes.
fn felder(mut daten: &[u8]) -> Result<Vec<(u64, &[u8])>, Fehler> {
    let mut aus = Vec::new();
    while !daten.is_empty() {
        let schluessel = varint(&mut daten)?;
        let (nr, art) = (schluessel >> 3, schluessel & 7);
        let breite = match art {
            0 => {
                varint(&mut daten)?;
                continue;
            }
            1 => 8,
            2 => usize::try_from(varint(&mut daten)?).map_err(|_| Fehler::Ungueltig)?,
            5 => 4,
            _ => return Err(Fehler::Ungueltig),
        };
        let wert = daten.get(..breite).ok_or(Fehler::Ungueltig)?;
        daten = &daten[breite..];
        if art == 2 {
            aus.push((nr, wert));
        }
    }
    Ok(aus)
}

fn varint(daten: &mut &[u8]) -> Result<u64, Fehler> {
    let mut wert = 0u64;
    for i in 0..10 {
        let (&b, rest) = daten.split_first().ok_or(Fehler::Ungueltig)?;
        *daten = rest;
        wert |= u64::from(b & 0x7f) << (7 * i);
        if b < 0x80 {
            return Ok(wert);
        }
    }
    Err(Fehler::Ungueltig)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use ring::rand::SystemRandom;
    use ring::signature::{EcdsaKeyPair, KeyPair, ECDSA_P256_SHA256_ASN1_SIGNING};

    fn feld(nr: u64, wert: &[u8]) -> Vec<u8> {
        let mut aus = Vec::new();
        let mut schreiben = |mut v: u64| loop {
            let b = (v & 0x7f) as u8;
            v >>= 7;
            if v == 0 {
                aus.push(b);
                break;
            }
            aus.push(b | 0x80);
        };
        schreiben(nr << 3 | 2);
        schreiben(wert.len() as u64);
        aus.extend_from_slice(wert);
        aus
    }

    /// Ein P-256-Schlüsselpaar und sein SPKI.
    pub(crate) fn schluessel() -> (EcdsaKeyPair, Vec<u8>) {
        let zufall = SystemRandom::new();
        let pkcs8 = EcdsaKeyPair::generate_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, &zufall).unwrap();
        let paar = EcdsaKeyPair::from_pkcs8(&ECDSA_P256_SHA256_ASN1_SIGNING, pkcs8.as_ref(), &zufall).unwrap();
        let punkt = paar.public_key().as_ref();
        let mut spki = vec![0x30, 0x59, 0x30, 0x13, 0x06, 0x07];
        spki.extend_from_slice(EC);
        spki.extend_from_slice(&[0x06, 0x08]);
        spki.extend_from_slice(P256);
        spki.extend_from_slice(&[0x03, 0x42, 0x00]);
        spki.extend_from_slice(punkt);
        (paar, spki)
    }

    /// Ein CRX3 mit Entwickler- und (wenn `store`) Store-Beweis über `zip`.
    pub(crate) fn bauen(entwickler: &(EcdsaKeyPair, Vec<u8>), store: Option<&(EcdsaKeyPair, Vec<u8>)>, zip: &[u8]) -> Vec<u8> {
        let hash = digest(&SHA256, &entwickler.1);
        let crx_id = &hash.as_ref()[..16];
        let signiert = feld(1, crx_id);
        let mut nachricht = KONTEXT.to_vec();
        nachricht.extend_from_slice(&(signiert.len() as u32).to_le_bytes());
        nachricht.extend_from_slice(&signiert);
        nachricht.extend_from_slice(zip);
        let zufall = SystemRandom::new();
        let mut kopf = Vec::new();
        for (paar, spki) in std::iter::once(entwickler).chain(store) {
            let signatur = paar.sign(&zufall, &nachricht).unwrap();
            kopf.extend(feld(3, &[feld(1, spki), feld(2, signatur.as_ref())].concat()));
        }
        kopf.extend(feld(10000, &signiert));
        let mut crx = b"Cr24".to_vec();
        crx.extend_from_slice(&3u32.to_le_bytes());
        crx.extend_from_slice(&(kopf.len() as u32).to_le_bytes());
        crx.extend(kopf);
        crx.extend_from_slice(zip);
        crx
    }

    fn hash(spki: &[u8]) -> [u8; 32] {
        digest(&SHA256, spki).as_ref().try_into().unwrap()
    }

    #[test]
    fn ein_paket_aus_dem_store_besteht() {
        let (dev, store) = (schluessel(), schluessel());
        let crx = bauen(&dev, Some(&store), b"PK\x03\x04inhalt");
        let id = id_aus_schluessel(&dev.1);
        let paket = pruefen(&crx, &id, &hash(&store.1)).unwrap();
        assert_eq!(paket.id, id);
        assert_eq!(paket.schluessel, dev.1);
        assert_eq!(paket.zip, b"PK\x03\x04inhalt");
        assert!(ist_id(&id));
    }

    #[test]
    fn ein_veraendertes_byte_im_zip_faellt_auf() {
        let (dev, store) = (schluessel(), schluessel());
        let mut crx = bauen(&dev, Some(&store), b"PK\x03\x04inhalt");
        *crx.last_mut().unwrap() ^= 1;
        assert_eq!(pruefen(&crx, &id_aus_schluessel(&dev.1), &hash(&store.1)).unwrap_err(), Fehler::Signatur);
    }

    #[test]
    fn ein_paket_einer_anderen_erweiterung_wird_abgelehnt() {
        let (dev, store, fremd) = (schluessel(), schluessel(), schluessel());
        let crx = bauen(&dev, Some(&store), b"PK\x03\x04");
        assert_eq!(pruefen(&crx, &id_aus_schluessel(&fremd.1), &hash(&store.1)).unwrap_err(), Fehler::FalscheId);
    }

    #[test]
    fn ohne_unterschrift_des_store_gilt_nichts() {
        let (dev, store) = (schluessel(), schluessel());
        let crx = bauen(&dev, None, b"PK\x03\x04");
        assert_eq!(pruefen(&crx, &id_aus_schluessel(&dev.1), &hash(&store.1)).unwrap_err(), Fehler::NichtAusDemStore);
        // Der echte Store-Schlüssel ist ein anderer als jeder Testschlüssel.
        let crx = bauen(&dev, Some(&store), b"PK\x03\x04");
        assert_eq!(pruefen(&crx, &id_aus_schluessel(&dev.1), &STORE_SCHLUESSEL).unwrap_err(), Fehler::NichtAusDemStore);
    }

    #[test]
    fn kaputte_koepfe_werden_abgelehnt() {
        let (dev, store) = (schluessel(), schluessel());
        let id = id_aus_schluessel(&dev.1);
        let gut = bauen(&dev, Some(&store), b"PK\x03\x04");
        let h = hash(&store.1);
        let mut v2 = gut.clone();
        v2[4] = 2;
        let mut zu_lang = gut.clone();
        zu_lang[8..12].copy_from_slice(&u32::MAX.to_le_bytes());
        for kaputt in [b"Cr2".to_vec(), v2, zu_lang, gut[..20].to_vec()] {
            assert_eq!(pruefen(&kaputt, &id, &h).unwrap_err(), Fehler::Ungueltig);
        }
    }

    #[test]
    fn eine_zip_endmarke_im_kopf_wird_abgelehnt() {
        let (dev, store) = (schluessel(), schluessel());
        let crx = bauen(&dev, Some(&store), b"PK\x03\x04");
        // Ein unbekanntes Feld mit einer Endmarke, eingeschoben in den Kopf.
        let laenge = u32::from_le_bytes(crx[8..12].try_into().unwrap()) as usize;
        let einschub = feld(4, b"PK\x05\x06");
        let mut neu = crx[..8].to_vec();
        neu.extend_from_slice(&((laenge + einschub.len()) as u32).to_le_bytes());
        neu.extend(einschub);
        neu.extend_from_slice(&crx[12..]);
        assert_eq!(pruefen(&neu, &id_aus_schluessel(&dev.1), &hash(&store.1)).unwrap_err(), Fehler::Ungueltig);
    }

    /// Gegen ein echtes Paket aus dem Web Store, von Hand:
    /// `MSB_CRX_PROBE=<datei>:<id> cargo test --lib echtes_paket -- --ignored`.
    #[test]
    #[ignore]
    fn echtes_paket_aus_dem_store() {
        let probe = std::env::var("MSB_CRX_PROBE").unwrap();
        let (datei, id) = probe.rsplit_once(':').unwrap();
        let crx = std::fs::read(datei).unwrap();
        let paket = pruefen(&crx, id, &STORE_SCHLUESSEL).unwrap();
        assert_eq!(paket.id, id);
        let mut kaputt = crx.clone();
        let mitte = crx.len() - 100;
        kaputt[mitte] ^= 1;
        assert_eq!(pruefen(&kaputt, id, &STORE_SCHLUESSEL).unwrap_err(), Fehler::Signatur);
    }
}
