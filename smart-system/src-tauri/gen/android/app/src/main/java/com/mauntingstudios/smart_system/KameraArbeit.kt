package com.mauntingstudios.smart_system

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.provider.MediaStore
import android.util.Base64
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.io.FileNotFoundException
import java.io.IOException
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Duration
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Die Kamera-Sicherung, auch bei geschlossener App und gesperrtem Tresor.
 *
 * Es gibt nur diesen einen Weg (AGENTS.md Punkt 32): auch bei offener App
 * sichert dieser Job. Jede neue Aufnahme aus DCIM wird zu einem Auftrag:
 * drei Blobs (Original Bit für Bit, Vorschau, Miniatur) mit eigenen, rohen
 * Schlüsseln und ein Datensatz für den Posteingang, hybrid verschlüsselt mit
 * dem öffentlichen Schlüssel des Tresors und vom Gerät unterschrieben (Format
 * wie `eingangVerpacken` in `tresorEingang.ts`, Krypto in `kamera_krypto.rs`).
 * Beim nächsten Entsperren macht eine App daraus einen Eintrag.
 *
 * Reihenfolge je Auftrag: Auftrag verschlüsselt ablegen, Marke weiter, Blobs
 * anlegen, Datensatz ablegen, Chunks hochladen (Fortsetzen über den Stand beim
 * Server), Original erst fertigmelden, wenn es noch dieselben Bytes hat. So
 * übernimmt keine App eine Datei, die sich beim Hochladen geändert hat; sie
 * wartet, bis alle drei Blobs fertig sind.
 */
class KameraArbeit(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    /** Dieser Lauf hört auf; der nächste setzt fort. */
    private class Abbruch : Exception()

    /** Der Auftrag lässt sich nicht beenden (Datei geändert, Blob beim Server weg). */
    private class Verloren : Exception()

    private val ctx = applicationContext
    private val zufall = SecureRandom()

    override fun doWork(): Result {
        val stand = KameraAblage.lesen(ctx) ?: return Result.success()
        val kennung = stand.kennung
        if (Medien.stand(ctx) != "voll") return warten(kennung, "zugriff")
        if (stand.nurWlan && !imWlan()) return warten(kennung, "wlan")

        val server = try {
            KameraServer(stand.server, KameraAblage.auf(stand.zugang))
        } catch (e: Exception) {
            return warten(kennung, "zugang")
        }
        return try {
            for (auftrag in KameraAblage.auftraege(ctx, kennung)) ausfuehren(server, kennung, auftrag)
            neueSichern(server, kennung)
            warten(kennung, null)
        } catch (e: Abbruch) {
            Result.success()
        } catch (e: KameraServer.Fehler) {
            when (e.status) {
                401 -> warten(kennung, "zugang")
                // Zurückgesetzt: dieser Tresor nimmt nichts mehr an. Die App sieht danach keine Sicherung mehr.
                410 -> {
                    if (KameraAblage.vergessen(ctx, kennung)) KameraPlan.abbestellen(ctx)
                    Result.success()
                }
                507 -> warten(kennung, "speicher")
                429, in 500..599 -> Result.retry()
                else -> warten(kennung, "fehler")
            }
        } catch (e: IOException) {
            Result.retry()
        } catch (e: Exception) {
            warten(kennung, "fehler")
        }
    }

    private fun warten(kennung: String, grund: String?): Result {
        KameraAblage.aendern(ctx, kennung) { it.copy(warten = grund) }
        return Result.success()
    }

    private fun weiter(kennung: String) {
        if (isStopped || KameraAblage.lesen(ctx)?.kennung != kennung) throw Abbruch()
    }

    /** Ohne Auskunft über das Netz gilt es nicht als WLAN (AGENTS.md Punkt 58). */
    private fun imWlan(): Boolean {
        val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return false
        val f = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
        return f.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
    }

    private fun neueSichern(server: KameraServer, kennung: String) {
        var stand = KameraAblage.lesen(ctx) ?: throw Abbruch()
        // Ein neu aufgebauter MediaStore zählt von vorn; mit der alten Marke fände die Sicherung nie wieder etwas.
        val (jetzt, fassung) = Medien.jetzt(ctx)
        if (fassung != stand.fassung) {
            stand = KameraAblage.aendern(ctx, kennung) { it.copy(marke = jetzt, markeId = KameraAblage.ALLE, fassung = fassung, screenshotsAb = jetzt) }
                ?: throw Abbruch()
        }
        while (true) {
            val aufnahmen = Medien.aufnahmen(ctx, stand.marke, stand.markeId, 20, stand.screenshotsAb.takeIf { stand.screenshots })
            if (aufnahmen.isEmpty()) return
            for (aufnahme in aufnahmen) {
                weiter(kennung)
                val auftrag = vorbereiten(kennung, aufnahme)
                if (auftrag != null && !KameraAblage.auftragSchreiben(ctx, kennung, auftrag)) throw Abbruch()
                // Weiter nur von der Stelle, an der dieser Lauf las. Hat die App sie inzwischen
                // zurückgesetzt („Vorhandene sichern“), gilt ihre, und es wird neu gelesen.
                val vorher = stand
                stand = KameraAblage.aendern(ctx, kennung) {
                    if (it.marke == vorher.marke && it.markeId == vorher.markeId) it.copy(marke = aufnahme.marke, markeId = aufnahme.id) else it
                } ?: throw Abbruch()
                if (auftrag != null) ausfuehren(server, kennung, auftrag)
                if (stand.marke != aufnahme.marke || stand.markeId != aufnahme.id) break
            }
        }
    }

    private fun hex(anzahl: Int) = Medien.hex(ByteArray(anzahl).also { zufall.nextBytes(it) })

    private fun kopf(groesse: Long, echt: Long, rolle: String) = JSONObject().apply {
        put("rolle", rolle); put("id", hex(16)); put("groesse", groesse); put("echt", echt)
        put("schluessel", hex(32)); put("loeschen", hex(32))
    }

    /** Ein Auftrag für eine Aufnahme, oder `null`, wenn sie schon gesichert ist, gerade gesichert wird oder weg ist. */
    private fun vorbereiten(kennung: String, aufnahme: Aufnahme): JSONObject? {
        val stand = KameraAblage.lesen(ctx)?.takeIf { it.kennung == kennung } ?: throw Abbruch()
        // Nur eine gelöschte Aufnahme wird übersprungen; jeder andere Lesefehler beendet
        // den Lauf, und die Stelle bleibt vor ihr.
        val (sha, groesse) = try {
            Medien.pruefsumme(ctx, aufnahme.id, aufnahme.art)
        } catch (e: FileNotFoundException) {
            return null
        }
        val schluessel = "${aufnahme.id}:$sha"
        if (schluessel in KameraAblage.bekannt(ctx)) return null
        if (KameraAblage.auftraege(ctx, kennung).any { "${it.getLong("medienId")}:${it.getString("sha")}" == schluessel }) return null

        val bilder = KameraBilder.angaben(ctx, aufnahme)
        val id = UUID.randomUUID().toString()
        val original = kopf(KameraKrypto.gepolstert(groesse), groesse, "original")
        val vorschau = kopf(KameraKrypto.VORSCHAU, bilder.vorschau.size.toLong(), "vorschau")
        val miniatur = kopf(KameraKrypto.MINIATUR, bilder.miniatur.size.toLong(), "miniatur")
        val name = aufnahme.name.ifEmpty { "${if (aufnahme.art == "video") "VID" else "IMG"}_${aufnahme.id}" }.take(255)

        fun ohneRolle(k: JSONObject) = JSONObject(k.toString()).apply { remove("rolle") }
        val inhalt = JSONObject().apply {
            put("name", name)
            put("typ", aufnahme.typ.take(255))
            put("geaendert", aufnahme.aufgenommen)
            bilder.aufgenommen?.let { put("aufgenommen", it) }
            bilder.kamera?.let { put("kamera", it) }
            bilder.breite?.let { put("breite", it) }
            bilder.hoehe?.let { put("hoehe", it) }
            bilder.dauer?.let { put("dauer", it) }
            put("original", ohneRolle(original))
            put("vorschau", ohneRolle(vorschau))
            put("miniatur", ohneRolle(miniatur))
            put("quelle", JSONObject().apply {
                put("geraet", stand.geraet); put("medienId", aufnahme.id); put("art", aufnahme.art); put("sha256", sha)
            })
        }
        return JSONObject().apply {
            put("id", id)
            put("medienId", aufnahme.id)
            put("art", aufnahme.art)
            put("sha", sha)
            // Kleine Blobs zuerst, das Original zuletzt: es wird erst fertiggemeldet, wenn es noch dieselben Bytes hat.
            put("blobs", JSONArray().put(miniatur).put(vorschau).put(original))
            put("vorschau", Base64.encodeToString(bilder.vorschau, Base64.NO_WRAP))
            put("miniatur", Base64.encodeToString(bilder.miniatur, Base64.NO_WRAP))
            put("umschlag", verpacken(stand, id, inhalt.toString()))
            put("angelegt", false)
        }
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

    private fun blobs(auftrag: JSONObject): List<JSONObject> {
        val liste = auftrag.getJSONArray("blobs")
        return (0 until liste.length()).map { liste.getJSONObject(it) }
    }

    private fun ausfuehren(server: KameraServer, kennung: String, auftrag: JSONObject) {
        try {
            hochladen(server, kennung, auftrag)
        } catch (e: Verloren) {
            verwerfen(server, auftrag)
            // Noch da (geändert oder beim Server aufgeräumt): einmal neu, mit den Bytes von jetzt.
            // Ein Auftrag ist schon zugelassen; hier zählt nur, ob die Aufnahme noch da ist.
            val aufnahme = Medien.aufnahme(ctx, auftrag.getLong("medienId"), auftrag.getString("art"), 0) ?: return
            val neu = vorbereiten(kennung, aufnahme) ?: return
            if (!KameraAblage.auftragSchreiben(ctx, kennung, neu)) throw Abbruch()
        }
    }

    private fun hochladen(server: KameraServer, kennung: String, auftrag: JSONObject) {
        val id = auftrag.getString("id")
        if (!auftrag.getBoolean("angelegt")) {
            for (b in blobs(auftrag)) {
                weiter(kennung)
                val groesse = b.getLong("groesse")
                val pruefwert = Medien.hex(MessageDigest.getInstance("SHA-256").digest(hexBytes(b.getString("loeschen"))))
                server.blobAnlegen(b.getString("id"), KameraKrypto.chunkAnzahl(groesse), KameraKrypto.chiffratGroesse(groesse), pruefwert)
            }
            server.eingangAblegen(id, auftrag.getString("umschlag"))
            auftrag.put("angelegt", true)
            if (!KameraAblage.auftragSchreiben(ctx, kennung, auftrag)) throw Abbruch()
        }
        for (b in blobs(auftrag)) {
            val blobId = b.getString("id")
            val stand = try {
                server.blobStand(blobId)
            } catch (e: KameraServer.Fehler) {
                if (e.status == 404) throw Verloren()
                throw e
            }
            if (stand.state == "fertig") continue
            // Gelöscht oder in der Löschhaltung: nimmt keine Chunks mehr an.
            if (stand.state != "offen") throw Verloren()
            val groesse = b.getLong("groesse")
            val echt = b.getLong("echt")
            val schluessel = hexBytes(b.getString("schluessel"))
            for (i in 0 until KameraKrypto.chunkAnzahl(groesse)) {
                if (i in stand.vorhanden) continue
                weiter(kennung)
                val daten = klartext(auftrag, b.getString("rolle"), groesse, echt, i)
                try {
                    server.chunk(blobId, i, KameraKrypto.chunk(schluessel, daten, groesse, echt, i, id, blobId))
                } catch (e: KameraServer.Fehler) {
                    // Betrifft nur diesen Blob (falsche Länge, schon fertig); voller Speicher, Netz und Sitzung träfen jeden (Punkt 76).
                    if (e.status == 404 || e.status == 409 || e.status == 413 || e.status == 422) throw Verloren()
                    throw e
                } finally {
                    daten.fill(0)
                }
            }
            schluessel.fill(0)
            if (b.getString("rolle") == "original") {
                val jetzt = try {
                    Medien.pruefsumme(ctx, auftrag.getLong("medienId"), auftrag.getString("art")).first
                } catch (e: Exception) {
                    null
                }
                if (jetzt != auftrag.getString("sha")) throw Verloren()
            }
            server.fertig(blobId)
        }
        KameraAblage.auftragEntfernen(ctx, id)
        KameraAblage.bekanntDazu(ctx, kennung, listOf("${auftrag.getLong("medienId")}:${auftrag.getString("sha")}"))
        KameraAblage.aendern(ctx, kennung) { it.copy(gesichert = it.gesichert + 1, zuletzt = System.currentTimeMillis(), warten = null) }
    }

    private fun klartext(auftrag: JSONObject, rolle: String, groesse: Long, echt: Long, index: Int): ByteArray {
        val laenge = KameraKrypto.echteBytes(groesse, echt, index)
        if (laenge == 0) return ByteArray(0)
        val von = index * KameraKrypto.CHUNK
        if (rolle != "original") {
            val bild = Base64.decode(auftrag.getString(rolle), Base64.NO_WRAP)
            return bild.copyOfRange(von.toInt(), von.toInt() + laenge)
        }
        val daten = try {
            Medien.lesen(ctx, auftrag.getLong("medienId"), auftrag.getString("art"), von, laenge)
        } catch (e: Exception) {
            throw Verloren()
        }
        if (daten.size != laenge) throw Verloren()
        return daten
    }

    /** Nimmt einen Auftrag zurück: Blobs mit dem Löschnachweis, den nur dieses Gerät kennt, und den Datensatz. */
    private fun verwerfen(server: KameraServer, auftrag: JSONObject) {
        for (b in blobs(auftrag)) server.blobLoeschen(b.getString("id"), b.getString("loeschen"))
        server.eingangLoeschen(auftrag.getString("id"))
        KameraAblage.auftragEntfernen(ctx, auftrag.getString("id"))
    }

    private fun hexBytes(text: String): ByteArray = ByteArray(text.length / 2) { text.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    companion object {
        private const val DOMAENE = "msm-tresor-eingang-v1"
        private const val FORMAT = 1
        private const val POLSTER = 4096
    }
}

/**
 * Hängt nur einen Lauf an und stellt sich selbst wieder auf. Ausgelöst von
 * neuen Aufnahmen (ContentUriTrigger) und einmal am Tag. Sie sichert nicht
 * selbst, damit nie zwei Läufe dieselbe Aufnahme bearbeiten.
 */
class KameraWache(ctx: Context, params: WorkerParameters) : Worker(ctx, params) {
    override fun doWork(): Result {
        if (KameraAblage.lesen(applicationContext) != null) {
            KameraPlan.anstossen(applicationContext)
            // Aus der laufenden Wache heraus anhängen (sie selbst ist noch nicht fertig); die tägliche ersetzt nur einen fehlenden.
            KameraPlan.ausloeserAufstellen(applicationContext, if (tags.contains(KameraPlan.AUSLOESER)) ExistingWorkPolicy.APPEND_OR_REPLACE else ExistingWorkPolicy.KEEP)
        }
        return Result.success()
    }
}

object KameraPlan {
    private const val LAUF = "msm-kamera-lauf"
    private const val WACHE = "msm-kamera-wache"
    private const val TAEGLICH = "msm-kamera-taeglich"
    const val AUSLOESER = "msm-kamera-ausloeser"

    /**
     * Ein Lauf zur Zeit. Ein Anstoß während eines Laufs hängt einen weiteren an
     * (AGENTS.md Punkt 64); `ersetzen` bricht den laufenden ab, etwa wenn sich
     * „nur im WLAN“ geändert hat. Ein abgebrochener Lauf setzt beim nächsten fort.
     */
    fun anstossen(ctx: Context, ersetzen: Boolean = false) {
        val stand = KameraAblage.lesen(ctx) ?: return
        val lauf = OneTimeWorkRequestBuilder<KameraArbeit>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(if (stand.nurWlan) NetworkType.UNMETERED else NetworkType.CONNECTED)
                    .build(),
            )
            .setBackoffCriteria(androidx.work.BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(LAUF, if (ersetzen) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE, lauf)
    }

    /** Wartet auf neue Bilder oder Videos und stößt dann einen Lauf an. */
    fun ausloeserAufstellen(ctx: Context, regel: ExistingWorkPolicy = ExistingWorkPolicy.REPLACE) {
        val wache = OneTimeWorkRequestBuilder<KameraWache>()
            .addTag(AUSLOESER)
            .setConstraints(
                Constraints.Builder()
                    .addContentUriTrigger(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, true)
                    .addContentUriTrigger(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, true)
                    .setTriggerContentUpdateDelay(Duration.ofSeconds(5))
                    .setTriggerContentMaxDelay(Duration.ofMinutes(1))
                    .build(),
            )
            .build()
        WorkManager.getInstance(ctx).enqueueUniqueWork(WACHE, regel, wache)
    }

    /** Nach dem Einrichten, nach einem Update und beim Start der App. */
    fun planen(ctx: Context) {
        if (KameraAblage.lesen(ctx) == null) return
        ausloeserAufstellen(ctx)
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            TAEGLICH,
            ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<KameraWache>(1, TimeUnit.DAYS).build(),
        )
        anstossen(ctx)
    }

    fun abbestellen(ctx: Context) {
        val wm = WorkManager.getInstance(ctx)
        wm.cancelUniqueWork(WACHE)
        wm.cancelUniqueWork(TAEGLICH)
        wm.cancelUniqueWork(LAUF)
    }
}
