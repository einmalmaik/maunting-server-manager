package com.mauntingstudios.smart_system

import android.app.ActivityManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkInfo
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONObject
import java.io.FileNotFoundException
import java.io.IOException
import java.security.MessageDigest
import java.time.Duration
import java.util.concurrent.TimeUnit

/**
 * Die Kamera-Sicherung, auch bei geschlossener App und gesperrtem Tresor.
 *
 * Es gibt nur diesen einen Weg (AGENTS.md Punkt 32): auch bei offener App
 * sichert dieser Job. Jedes neue Foto und Video (`Medien.auswahl`) wird zu einem Auftrag:
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

    override fun doWork(): Result {
        val stand = KameraAblage.lesen(ctx) ?: return Result.success()
        val kennung = stand.kennung
        KameraTeilen.aufraeumen(ctx, kennung)
        if (Medien.stand(ctx) != "voll") return warten(kennung, "zugriff")
        if (stand.nurWlan && !imWlan(ctx)) return warten(kennung, "wlan")

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
                429, in 500..599 -> spaeter(kennung)
                else -> warten(kennung, "fehler")
            }
        } catch (e: IOException) {
            spaeter(kennung)
        } catch (e: Exception) {
            warten(kennung, "fehler")
        }
    }

    /**
     * Netz oder Server gerade weg. Kein `Result.retry()`: der Lauf wartete dann
     * mit wachsendem Abstand (bis zu Stunden), und jeder Anstoß, auch eine neue
     * Aufnahme oder die geöffnete App, hing sich dahinter. So endet er, ein
     * neuer Versuch kommt nach `SPAETER_MINUTEN`, und jeder Anstoß läuft sofort.
     */
    private fun spaeter(kennung: String): Result {
        KameraPlan.spaeter(ctx)
        return warten(kennung, "server")
    }

    private fun warten(kennung: String, grund: String?): Result {
        KameraAblage.aendern(ctx, kennung) { it.copy(warten = grund) }
        return Result.success()
    }

    private fun weiter(kennung: String) {
        if (isStopped || KameraAblage.lesen(ctx)?.kennung != kennung) throw Abbruch()
    }

    private fun neueSichern(server: KameraServer, kennung: String) {
        var stand = KameraAblage.lesen(ctx) ?: throw Abbruch()
        // Ein neu aufgebauter MediaStore zählt von vorn; mit der alten Marke fände die Sicherung nie wieder etwas.
        val (jetzt, fassung) = Medien.jetzt(ctx)
        if (fassung != stand.fassung) {
            stand = KameraAblage.aendern(ctx, kennung) { it.copy(marke = jetzt, markeId = KameraAblage.ALLE, fassung = fassung, screenshotsAb = jetzt, weitereAb = jetzt) }
                ?: throw Abbruch()
        }
        // Nach dem Update, das die weiteren Ordner brachte: ab jetzt, nicht bis zur letzten Kameraaufnahme zurück.
        if (stand.weitereAb == KameraAblage.NEU) {
            stand = KameraAblage.aendern(ctx, kennung) { if (it.weitereAb == KameraAblage.NEU) it.copy(weitereAb = jetzt) else it } ?: throw Abbruch()
        }
        while (true) {
            val aufnahmen = Medien.aufnahmen(ctx, stand.marke, stand.markeId, 20, stand.weitereAb, stand.screenshotsAb.takeIf { stand.screenshots })
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
        val bekannt = KameraAblage.bekannt(ctx)
        // `0:` gilt für jede Kennung: eine Datei, die aus dem Tresor aufs Telefon gespeichert oder in ihn geteilt wurde.
        if (schluessel in bekannt || "0:$sha" in bekannt) return null
        // Ebenso, wenn dieselbe Datei gerade als geteilte Datei unterwegs ist.
        if (KameraAblage.auftraege(ctx, kennung).any {
                (it.has("lokal") && it.getString("sha") == sha) || "${it.optLong("medienId")}:${it.getString("sha")}" == schluessel
            }
        ) return null

        val video = aufnahme.art == "video"
        val bilder = KameraBilder.angaben(ctx, if (video) Medien.adresse(aufnahme.id, aufnahme.art) else Medien.original(aufnahme.id, aufnahme.art), video)
        val name = aufnahme.name.ifEmpty { "${if (video) "VID" else "IMG"}_${aufnahme.id}" }.take(255)
        val angaben = JSONObject().apply {
            put("name", name)
            put("typ", aufnahme.typ.take(255))
            put("geaendert", aufnahme.aufgenommen)
            put("quelle", JSONObject().apply {
                put("geraet", stand.geraet); put("medienId", aufnahme.id); put("art", aufnahme.art); put("sha256", sha)
            })
        }
        return KameraAuftrag.bauen(stand, angaben, bilder, groesse, sha, JSONObject().put("medienId", aufnahme.id).put("art", aufnahme.art))
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
            val neu = if (auftrag.has("lokal")) {
                // Geteilt: die verschlüsselte Kopie liegt noch auf dem Telefon, neue Blobs für sie.
                val stand = KameraAblage.lesen(ctx)?.takeIf { it.kennung == kennung } ?: throw Abbruch()
                if (!KameraTeilen.vorhanden(ctx, auftrag)) return
                KameraAuftrag.neu(stand, auftrag)
            } else {
                // Noch da (geändert oder beim Server aufgeräumt): einmal neu, mit den Bytes von jetzt.
                // Ein Auftrag ist schon zugelassen; hier zählt nur, ob die Aufnahme noch da ist.
                val aufnahme = Medien.aufnahme(ctx, auftrag.getLong("medienId"), auftrag.getString("art")) ?: return
                vorbereiten(kennung, aufnahme) ?: return
            }
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
            // Genullt auf jedem Weg, auch wenn ein Chunk scheitert oder der Lauf abbricht.
            val schluessel = hexBytes(b.getString("schluessel"))
            try {
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
            } finally {
                schluessel.fill(0)
            }
            if (b.getString("rolle") == "original" && !auftrag.has("lokal")) {
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
        if (auftrag.has("lokal")) {
            KameraTeilen.entfernen(ctx, auftrag)
            // Liegt dieselbe Datei in einem gesicherten Ordner, geht sie nicht noch einmal hoch.
            KameraAblage.bekanntDazu(ctx, kennung, listOf("0:${auftrag.getString("sha")}"))
        } else {
            KameraAblage.bekanntDazu(ctx, kennung, listOf("${auftrag.getLong("medienId")}:${auftrag.getString("sha")}"))
        }
        KameraAblage.aendern(ctx, kennung) { it.copy(gesichert = it.gesichert + 1, zuletzt = System.currentTimeMillis(), warten = null) }
    }

    private fun klartext(auftrag: JSONObject, rolle: String, groesse: Long, echt: Long, index: Int): ByteArray {
        val laenge = KameraKrypto.echteBytes(groesse, echt, index)
        if (laenge == 0) return ByteArray(0)
        val von = index * KameraKrypto.CHUNK
        if (rolle != "original") {
            val bild = Base64.decode(auftrag.getString(rolle), Base64.NO_WRAP)
            try {
                return bild.copyOfRange(von.toInt(), von.toInt() + laenge)
            } finally {
                bild.fill(0)
            }
        }
        val daten = try {
            if (auftrag.has("lokal")) {
                KameraTeilen.lesen(ctx, auftrag, index)
            } else {
                Medien.lesen(ctx, auftrag.getLong("medienId"), auftrag.getString("art"), von, laenge)
            }
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
        /** Ohne Auskunft über das Netz gilt es nicht als WLAN (AGENTS.md Punkt 58). */
        fun imWlan(ctx: Context): Boolean {
            val cm = ctx.getSystemService(ConnectivityManager::class.java) ?: return false
            val f = cm.getNetworkCapabilities(cm.activeNetwork ?: return false) ?: return false
            return f.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
        }

        /**
         * Ob Android die App im Hintergrund festhält: „Eingeschränkt“ bei Samsung,
         * Energiesparen bei Xiaomi und anderen setzen genau diese Sperre. Der Job
         * läuft dann erst, wenn die App offen ist, und kann das selbst nicht melden.
         */
        fun gebremst(ctx: Context): Boolean {
            if (Build.VERSION.SDK_INT >= 28 && ctx.getSystemService(ActivityManager::class.java)?.isBackgroundRestricted == true) return true
            if (Build.VERSION.SDK_INT >= 30) {
                val stufe = ctx.getSystemService(UsageStatsManager::class.java)?.appStandbyBucket
                return stufe == UsageStatsManager.STANDBY_BUCKET_RESTRICTED
            }
            return false
        }

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
    private const val SPAETER = "msm-kamera-spaeter"
    private const val SPAETER_MINUTEN = 15L
    const val AUSLOESER = "msm-kamera-ausloeser"

    /**
     * Ein Lauf zur Zeit. Ein Anstoß während eines Laufs hängt einen weiteren an
     * (AGENTS.md Punkt 64); `ersetzen` bricht den laufenden ab, etwa wenn sich
     * „nur im WLAN“ geändert hat. Ein abgebrochener Lauf setzt beim nächsten fort.
     *
     * Ein Lauf, der nur wartet (Netzbedingung, Rückzug einer älteren Fassung),
     * wird ersetzt: angehängt hinge der Anstoß dahinter, und eine neue Aufnahme
     * wartete Stunden. Fragt WorkManager und blockiert dabei; nie im Hauptthread.
     */
    fun anstossen(ctx: Context, ersetzen: Boolean = false) {
        val stand = KameraAblage.lesen(ctx) ?: return
        val wm = WorkManager.getInstance(ctx)
        val laeuft = wm.getWorkInfosForUniqueWork(LAUF).get().any { it.state == WorkInfo.State.RUNNING }
        val lauf = OneTimeWorkRequestBuilder<KameraArbeit>()
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(if (stand.nurWlan) NetworkType.UNMETERED else NetworkType.CONNECTED)
                    .build(),
            )
            .build()
        wm.enqueueUniqueWork(LAUF, if (ersetzen || !laeuft) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE, lauf)
    }

    /** Nächster Versuch nach einem Netz- oder Serverfehler; ersetzt einen schon geplanten. */
    fun spaeter(ctx: Context) {
        WorkManager.getInstance(ctx).enqueueUniqueWork(
            SPAETER,
            ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<KameraWache>().setInitialDelay(SPAETER_MINUTEN, TimeUnit.MINUTES).build(),
        )
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
        val an = KameraAblage.lesen(ctx) != null
        KameraTeilen.zielSetzen(ctx, an)
        if (!an) return
        ausloeserAufstellen(ctx)
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(
            TAEGLICH,
            ExistingPeriodicWorkPolicy.KEEP,
            PeriodicWorkRequestBuilder<KameraWache>(1, TimeUnit.DAYS).build(),
        )
        anstossen(ctx)
    }

    fun abbestellen(ctx: Context) {
        KameraTeilen.zielSetzen(ctx, false)
        val wm = WorkManager.getInstance(ctx)
        wm.cancelUniqueWork(WACHE)
        wm.cancelUniqueWork(TAEGLICH)
        wm.cancelUniqueWork(LAUF)
        wm.cancelUniqueWork(SPAETER)
    }
}
