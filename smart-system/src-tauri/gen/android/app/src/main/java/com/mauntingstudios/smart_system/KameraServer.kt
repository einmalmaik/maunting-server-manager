package com.mauntingstudios.smart_system

import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Die Routen der Kamera-Sicherung unter `/api/vault/sicherung/`. Der Zugang
 * steht im Kopf `X-MSM-Sicherung`, nie in einer Adresse und nie im Log.
 * Lesen kann er nichts: Blobs anlegen und füllen, Datensätze ablegen und
 * eigene wieder löschen.
 */
class KameraServer(server: String, private val zugang: String) {
    /** Antwort mit Fehlerstatus; `code` aus `detail.code`, wenn der Server einen nennt. */
    class Fehler(val status: Int, val code: String?) : IOException("HTTP $status")

    data class BlobStand(val state: String, val vorhanden: Set<Int>)

    private val basis = server.trimEnd('/') + "/api/vault/sicherung"

    private fun anfrage(methode: String, pfad: String, json: JSONObject? = null, bytes: ByteArray? = null): String {
        val verbindung = URL(basis + pfad).openConnection() as HttpURLConnection
        try {
            verbindung.requestMethod = methode
            verbindung.connectTimeout = 15_000
            verbindung.readTimeout = 60_000
            verbindung.instanceFollowRedirects = false
            verbindung.useCaches = false
            verbindung.setRequestProperty("X-MSM-Sicherung", zugang)
            verbindung.setRequestProperty("Accept", "application/json")
            val koerper = when {
                bytes != null -> { verbindung.setRequestProperty("Content-Type", "application/octet-stream"); bytes }
                json != null -> { verbindung.setRequestProperty("Content-Type", "application/json"); json.toString().toByteArray() }
                else -> null
            }
            if (koerper != null) {
                verbindung.doOutput = true
                verbindung.setFixedLengthStreamingMode(koerper.size)
                verbindung.outputStream.use { it.write(koerper) }
            }
            val status = verbindung.responseCode
            val strom = if (status in 200..299) verbindung.inputStream else verbindung.errorStream
            val text = strom?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            if (status !in 200..299) {
                val code = try {
                    JSONObject(text).optJSONObject("detail")?.optString("code")?.takeIf { it.isNotEmpty() }
                } catch (e: Exception) {
                    null
                }
                throw Fehler(status, code)
            }
            return text
        } finally {
            verbindung.disconnect()
        }
    }

    /** Legt einen Blob an. Gibt es ihn schon (Antwort beim letzten Mal verloren), gilt er als angelegt, wenn er in diesem Tresor liegt. */
    fun blobAnlegen(id: String, chunkCount: Int, bytesTotal: Long, deleteVerifier: String) {
        try {
            anfrage("POST", "/blobs", JSONObject().apply {
                put("id", id); put("chunk_count", chunkCount); put("bytes_total", bytesTotal); put("delete_verifier", deleteVerifier)
            })
        } catch (e: Fehler) {
            if (e.status != 409) throw e
            blobStand(id)
        }
    }

    fun blobStand(id: String): BlobStand {
        val j = JSONObject(anfrage("GET", "/blobs/$id/status"))
        val liste = j.optJSONArray("vorhanden")
        val vorhanden = (0 until (liste?.length() ?: 0)).map { liste!!.getInt(it) }.toSet()
        return BlobStand(j.getString("state"), vorhanden)
    }

    fun chunk(id: String, index: Int, daten: ByteArray) {
        anfrage("PUT", "/blobs/$id/chunks/$index", bytes = daten)
    }

    fun fertig(id: String) {
        anfrage("POST", "/blobs/$id/fertig")
    }

    fun blobLoeschen(id: String, loeschen: String) {
        try {
            anfrage("DELETE", "/blobs/$id", JSONObject().put("schluessel", loeschen))
        } catch (e: Fehler) {
            if (e.status != 404) throw e
        }
    }

    /** Dieselbe Kennung mit demselben Inhalt nimmt der Server ein zweites Mal ohne Fehler an. */
    fun eingangAblegen(id: String, umschlag: String) {
        anfrage("POST", "/eingang", JSONObject().put("id", id).put("ciphertext", umschlag))
    }

    fun eingangLoeschen(id: String) {
        anfrage("DELETE", "/eingang/$id")
    }
}
