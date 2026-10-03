package com.mauntingstudios.smart_system

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.SecureRandom
import java.util.UUID

/**
 * Baut einen Auftrag der Kamera-Sicherung: drei Blobs (Original Bit für Bit,
 * Vorschau, Miniatur) mit eigenen, rohen Schlüsseln und ein Datensatz für den
 * Posteingang, hybrid verschlüsselt mit dem öffentlichen Schlüssel des Tresors
 * und vom Gerät unterschrieben (Format wie `eingangVerpacken` in
 * `tresorEingang.ts`, Krypto in `kamera_krypto.rs`).
 *
 * Gemeinsam für Aufnahmen aus dem MediaStore (`KameraArbeit`) und Dateien, die
 * aus einer anderen App geteilt wurden (`KameraTeilen`). Woher das Original
 * kommt, steht in `herkunft`: `medienId` und `art`, oder `lokal`.
 */
object KameraAuftrag {
    private const val DOMAENE = "msm-tresor-eingang-v1"
    private const val FORMAT = 1
    private const val POLSTER = 4096

    private val zufall = SecureRandom()

    private fun hex(anzahl: Int) = Medien.hex(ByteArray(anzahl).also { zufall.nextBytes(it) })

    private fun kopf(groesse: Long, echt: Long, rolle: String) = JSONObject().apply {
        put("rolle", rolle); put("id", hex(16)); put("groesse", groesse); put("echt", echt)
        put("schluessel", hex(32)); put("loeschen", hex(32))
    }

    /**
     * `angaben`: Name, Typ und Zeiten, bei Aufnahmen auch die Quelle; ohne
     * Blobs. Sie bleiben im Auftrag (`angaben`), damit eine geteilte Datei
     * nach einem verlorenen Upload neu aufgesetzt werden kann.
     */
    fun bauen(
        stand: KameraAblage.Stand,
        angaben: JSONObject,
        bilder: KameraBilder.Angaben,
        groesse: Long,
        sha: String,
        herkunft: JSONObject,
    ): JSONObject {
        val id = UUID.randomUUID().toString()
        val original = kopf(KameraKrypto.gepolstert(groesse), groesse, "original")
        val vorschau = kopf(KameraKrypto.VORSCHAU, bilder.vorschau.size.toLong(), "vorschau")
        val miniatur = kopf(KameraKrypto.MINIATUR, bilder.miniatur.size.toLong(), "miniatur")

        fun ohneRolle(k: JSONObject) = JSONObject(k.toString()).apply { remove("rolle") }
        val inhalt = JSONObject(angaben.toString()).apply {
            bilder.aufgenommen?.let { put("aufgenommen", it) }
            bilder.kamera?.let { put("kamera", it) }
            bilder.breite?.let { put("breite", it) }
            bilder.hoehe?.let { put("hoehe", it) }
            bilder.dauer?.let { put("dauer", it) }
            put("original", ohneRolle(original))
            put("vorschau", ohneRolle(vorschau))
            put("miniatur", ohneRolle(miniatur))
        }
        return JSONObject(herkunft.toString()).apply {
            put("id", id)
            put("sha", sha)
            put("groesse", groesse)
            put("angaben", angaben)
            // Kleine Blobs zuerst, das Original zuletzt: es wird erst fertiggemeldet, wenn es noch dieselben Bytes hat.
            put("blobs", JSONArray().put(miniatur).put(vorschau).put(original))
            put("vorschau", Base64.encodeToString(bilder.vorschau, Base64.NO_WRAP))
            put("miniatur", Base64.encodeToString(bilder.miniatur, Base64.NO_WRAP))
            put("umschlag", verpacken(stand, id, inhalt.toString()))
            put("angelegt", false)
        }
    }

    /** Ein neuer Auftrag für dasselbe Original, mit neuen Blobs und Schlüsseln. */
    fun neu(stand: KameraAblage.Stand, alt: JSONObject): JSONObject {
        val bilder = KameraBilder.Angaben().apply {
            vorschau = Base64.decode(alt.getString("vorschau"), Base64.NO_WRAP)
            miniatur = Base64.decode(alt.getString("miniatur"), Base64.NO_WRAP)
        }
        val herkunft = JSONObject().put("lokal", alt.getJSONObject("lokal"))
        return bauen(stand, alt.getJSONObject("angaben"), bilder, alt.getLong("groesse"), alt.getString("sha"), herkunft)
    }

    /** Wie `eingangVerpacken` in `tresorEingang.ts`. */
    private fun verpacken(stand: KameraAblage.Stand, eingangId: String, json: String): String {
        // Leerzeichen am Ende ändern nichts an JSON.parse; Namen sollen nicht an der Länge auffallen.
        // Gezählt in UTF-8-Bytes, so wie sie verschlüsselt werden.
        val roh = json.toByteArray(Charsets.UTF_8)
        val gepolstert = roh.copyOf(((roh.size + 1 + POLSTER - 1) / POLSTER) * POLSTER)
        gepolstert.fill(' '.code.toByte(), roh.size)
        roh.fill(0)
        val chiffrat = try {
            KameraKrypto.hybrid(
                gepolstert,
                Base64.decode(stand.pq, Base64.NO_WRAP),
                Base64.decode(stand.rsa, Base64.NO_WRAP),
                "$DOMAENE:${stand.bucket}:$eingangId".toByteArray(Charsets.UTF_8),
            )
        } finally {
            gepolstert.fill(0)
        }
        val daten = Base64.encodeToString(chiffrat, Base64.NO_WRAP)
        val signiert = listOf(DOMAENE, FORMAT.toString(), stand.bucket, eingangId, stand.eingangId, stand.geraet, daten).joinToString("\n")
        val signatur = SicherungsSchluessel.unterschreiben(stand.geraet, signiert.toByteArray(Charsets.UTF_8))
        return JSONObject().apply {
            put("v", FORMAT); put("schluessel", stand.eingangId); put("geraet", stand.geraet); put("daten", daten)
            put("signatur", Base64.encodeToString(signatur, Base64.NO_WRAP))
        }.toString()
    }
}
