package com.mauntingstudios.smart_system

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/**
 * Der Dauerdienst: hört zu, auch wenn die App geschlossen ist.
 *
 * ## Warum ein Vordergrunddienst mit eigener Verbindung
 *
 * Eine beendete App hat keine Verbindung. Auf einem Android-Gerät gibt es drei
 * Wege, trotzdem etwas zu erfahren: über Google (FCM), über eine zweite App
 * (UnifiedPush-Verteiler) oder über die App selbst, die eine Verbindung hält.
 * Dieser Dienst ist der dritte Weg — kein Google, keine zweite Installation.
 *
 * Der Preis steht in der Benachrichtigung, die das System verlangt: „läuft im
 * Hintergrund". Sie ist nicht abschaltbar, und ohne Akku-Ausnahme beenden
 * Samsung und Xiaomi den Dienst nach Stunden. Beides ist ehrlicher als die
 * Behauptung, es gehe ohne.
 *
 * ## Warum ein eigener Prozess
 *
 * `MainActivity` beendet beim Schließen absichtlich den Prozess, damit die
 * Tauri-Umgebung beim nächsten Öffnen frisch startet. Ein Dienst im selben
 * Prozess stürbe mit. Deshalb steht im Manifest `android:process=":dienst"`
 * (Punkt 20 der Projektregeln: was offen ist, muss auch offen bleiben).
 *
 * Die Folge: zwei Prozesse teilen sich **keine** Ablage. Die Oberfläche
 * schreibt, was der Mensch geschaltet hat (`PREFS`), der Dienst schreibt, was
 * er zuletzt bekam (`PREFS_LAUF`). Was sich ändert, reist als Intent-Extra —
 * eine SharedPreferences-Datei, die beide Prozesse schreiben, verlöre Werte.
 *
 * ## Warum ein eigener Ausweis
 *
 * Der Zugangstoken des Panels lebt 15 Minuten; ein Dienst, der sich damit
 * verbindet, verstummt nach einer Viertelstunde. Er benutzt deshalb den
 * Kanal-Ausweis aus `POST /notifications/channels`, der gilt, bis er widerrufen
 * wird. Erneuert wird er hier nie — zwei Rotationen auf derselben Familie
 * würden sie sperren (AGENTS.md Punkt 62).
 *
 * ## Was er meldet — und wann nicht
 *
 * Jedes Ereignis trägt `melden`. Der Server wählt **einen** Zusteller je Konto:
 * das offene Fenster, sonst diesen Dienst. Steht hier `false`, zeigt ein
 * anderes Gerät oder Fenster die Nachricht bereits; dann bleibt der Dienst
 * still. Ohne Angabe (älterer Server) gilt „melden", damit nichts verloren geht.
 *
 * Ein Inhalt steht nie in der Meldung — der Strom trägt für E2EE nur die
 * Nachricht „es liegt etwas vor".
 */
class Hintergrunddienst : Service() {

    companion object {
        private const val TAG = "MsmDienst"
        const val KANAL_STATUS = "msm_dienst_status"
        const val KANAL_MELDUNG = "msm_meldungen"
        private const val ID_STATUS = 4711

        /** Was der Mensch geschaltet hat. Geschrieben nur von der Oberfläche. */
        private const val PREFS = "msm_dienst"

        /** Was der Dienst zuletzt bekam. Geschrieben nur vom Dienst. */
        private const val PREFS_LAUF = "msm_dienst_lauf"

        private const val KEY_AKTIV = "aktiv"
        private const val KEY_AUSWEIS = "ausweis"
        private const val KEY_BASIS = "basis"
        private const val KEY_MAILBOXEN = "mailboxen"
        private const val KEY_GESEHEN = "gesehen"
        private const val GESEHEN_MAX = 100

        const val EXTRA_AUSWEIS = "ausweis"
        const val EXTRA_BASIS = "basis"
        const val EXTRA_MAILBOXEN = "mailboxen"

        fun einstellungen(ctx: Context) =
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        /** Ablage des Dienstes. Nur aus dem Dienstprozess lesen und schreiben. */
        fun laufAblage(ctx: Context) =
            ctx.getSharedPreferences(PREFS_LAUF, Context.MODE_PRIVATE)

        /** Hat der Mensch ihn eingeschaltet und liegt ein Ausweis vor? */
        fun istEingeschaltet(ctx: Context): Boolean {
            val prefs = einstellungen(ctx)
            return prefs.getBoolean(KEY_AKTIV, false) &&
                (prefs.getString(KEY_AUSWEIS, "") ?: "").length == 64
        }

        fun einrichten(ctx: Context, ausweis: String, basis: String, mailboxen: String) {
            einstellungen(ctx).edit()
                .putBoolean(KEY_AKTIV, true)
                .putString(KEY_AUSWEIS, ausweis)
                .putString(KEY_BASIS, basis.trimEnd('/'))
                .putString(KEY_MAILBOXEN, mailboxen)
                .apply()
        }

        /**
         * Startet den Dienst mit dem, was die Oberfläche hinterlegt hat.
         *
         * Alles reist im Intent: der Dienstprozess hat seine eigene Ablage und
         * sähe einen früheren Eintrag in der Ablage der Oberfläche nicht.
         */
        fun starten(ctx: Context) {
            val prefs = einstellungen(ctx)
            if (!istEingeschaltet(ctx)) return
            val absicht = Intent(ctx, Hintergrunddienst::class.java)
                .putExtra(EXTRA_AUSWEIS, prefs.getString(KEY_AUSWEIS, "") ?: "")
                .putExtra(EXTRA_BASIS, prefs.getString(KEY_BASIS, "") ?: "")
                .putExtra(EXTRA_MAILBOXEN, prefs.getString(KEY_MAILBOXEN, "[]") ?: "[]")
            starte(ctx, absicht)
        }

        /**
         * Neue Mailboxliste: sofort neu verbinden, damit sie gilt.
         *
         * Ohne eingeschalteten Dienst nur merken — ein Chatwechsel darf keinen
         * Dienst starten, den jemand ausgeschaltet hat.
         */
        fun mailboxenSetzen(ctx: Context, mailboxen: String) {
            einstellungen(ctx).edit().putString(KEY_MAILBOXEN, mailboxen).apply()
            if (!istEingeschaltet(ctx)) return
            // Aus dem Hintergrund darf Android den Start verbieten (ab 12).
            // Die Liste ist gehalten; der nächste Verbindungsaufbau nimmt sie
            // mit — ein Chatwechsel darf die App nicht beenden.
            runCatching {
                starte(
                    ctx,
                    Intent(ctx, Hintergrunddienst::class.java).putExtra(EXTRA_MAILBOXEN, mailboxen),
                )
            }.onFailure { Log.w(TAG, "Dienst nicht angestoßen: ${it.message}") }
        }

        /** Aus: Merker weg, Dienst weg. Der Ausweis wird woanders widerrufen. */
        fun stoppen(ctx: Context) {
            einstellungen(ctx).edit().putBoolean(KEY_AKTIV, false).apply()
            ctx.stopService(Intent(ctx, Hintergrunddienst::class.java))
        }

        /** Nach Neustart oder Update — nur, wenn er eingeschaltet war. */
        fun nachNeustart(ctx: Context) {
            if (istEingeschaltet(ctx)) starten(ctx)
        }

        private fun starte(ctx: Context, absicht: Intent) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                ctx.startForegroundService(absicht)
            } else {
                ctx.startService(absicht)
            }
        }
    }

    private var faden: Thread? = null

    @Volatile private var laeuft = false

    @Volatile private var verbindung: HttpURLConnection? = null

    @Volatile private var neuVerbinden = false

    private class AusweisUngueltig : Exception("Der Kanal-Ausweis gilt nicht mehr")

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        kanaeleAnlegen()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val geaendert = nehmeAn(intent)
        startForeground(ID_STATUS, statusMeldung())
        if (faden == null) {
            laeuft = true
            faden = Thread({ schleife() }, "msm-hintergrunddienst").also { it.start() }
        } else if (geaendert) {
            // Neue Angaben: die offene Verbindung kennt sie nicht.
            neuVerbinden = true
            runCatching { verbindung?.disconnect() }
        }
        // START_STICKY: killt das System den Prozess, startet Android ihn neu.
        return START_STICKY
    }

    /**
     * Übernimmt, was der Start mitbringt.
     *
     * Gibt zurück, ob sich etwas geändert hat. Ein Neustart durch das System
     * bringt nichts mit (`intent == null`); dann gilt die eigene Ablage, und
     * genau dafür steht sie da.
     */
    private fun nehmeAn(intent: Intent?): Boolean {
        if (intent == null) return false
        val neu = mutableMapOf<String, String>()
        intent.getStringExtra(EXTRA_AUSWEIS)?.let { neu[KEY_AUSWEIS] = it }
        intent.getStringExtra(EXTRA_BASIS)?.let { neu[KEY_BASIS] = it.trimEnd('/') }
        intent.getStringExtra(EXTRA_MAILBOXEN)?.let { neu[KEY_MAILBOXEN] = it }
        if (neu.isEmpty()) return false
        val edit = laufAblage(this).edit()
        for ((schluessel, wert) in neu) edit.putString(schluessel, wert)
        edit.apply()
        return true
    }

    override fun onDestroy() {
        laeuft = false
        runCatching { verbindung?.disconnect() }
        faden?.interrupt()
        faden = null
        super.onDestroy()
    }

    // ── Verbindung ─────────────────────────────────────────────────────────

    private fun schleife() {
        var wartezeit = 5_000L
        while (laeuft) {
            val prefs = laufAblage(this)
            val ausweis = prefs.getString(KEY_AUSWEIS, "") ?: ""
            val basis = prefs.getString(KEY_BASIS, "") ?: ""
            if (ausweis.length != 64 || basis.isEmpty()) {
                stopSelf()
                return
            }
            try {
                verbinde(basis, ausweis)
                wartezeit = 5_000L
            } catch (e: AusweisUngueltig) {
                ausweisAbgelaufen()
                return
            } catch (e: Exception) {
                if (!laeuft) return
                Log.w(TAG, "Verbindung beendet: ${e.message}")
            } finally {
                runCatching { verbindung?.disconnect() }
                verbindung = null
            }
            if (!laeuft) return
            if (neuVerbinden) {
                neuVerbinden = false
                wartezeit = 0
            }
            try {
                if (wartezeit > 0) Thread.sleep(wartezeit)
            } catch (e: InterruptedException) {
                return
            }
            wartezeit = (wartezeit * 2).coerceAtMost(60_000L)
        }
    }

    /**
     * Liest den Ereignisstrom, bis die Verbindung endet.
     *
     * Der Server schickt alle 15 Sekunden ein Keepalive (`: ping`); der
     * Lesetimeout liegt darüber, damit ein stiller Abbruch auffällt statt
     * ewig zu warten.
     */
    private fun verbinde(basis: String, ausweis: String) {
        val conn = URL("$basis/api/events/live").openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.setRequestProperty("Authorization", "Bearer $ausweis")
        conn.setRequestProperty("Accept", "text/event-stream")
        conn.setRequestProperty("Cache-Control", "no-cache")
        conn.connectTimeout = 15_000
        conn.readTimeout = 45_000
        verbindung = conn

        val status = conn.responseCode
        if (status == 401 || status == 403) throw AusweisUngueltig()
        if (status != 200) throw IllegalStateException("HTTP $status")

        conn.inputStream.bufferedReader().use { leser ->
            var ereignis = "message"
            val daten = StringBuilder()
            while (laeuft) {
                val zeile = leser.readLine() ?: break
                when {
                    zeile.startsWith("event:") -> ereignis = zeile.removePrefix("event:").trim()
                    zeile.startsWith("data:") -> daten.appendLine(zeile.removePrefix("data:").trim())
                    zeile.isBlank() -> {
                        val inhalt = daten.toString()
                        daten.setLength(0)
                        val art = ereignis
                        ereignis = "message"
                        when (art) {
                            "ready" -> stromBereit(basis, ausweis, inhalt)
                            "sync" -> verarbeiten(inhalt)
                            "shutdown" -> {
                                // Der Ausweis wurde widerrufen oder das Panel
                                // fährt herunter. Nicht in einer Schleife
                                // gegen eine geschlossene Tür laufen.
                                ausweisAbgelaufen()
                                return
                            }
                        }
                    }
                }
            }
        }
    }

    /** Nach `ready`: sagen, wer wir sind und was wir hören wollen. */
    private fun stromBereit(basis: String, ausweis: String, inhalt: String) {
        val json = runCatching { JSONObject(inhalt) }.getOrNull() ?: return
        val connId = json.optString("conn_id", "")
        if (connId.isEmpty()) return
        post(basis, ausweis, "/api/events/presence", JSONObject().apply {
            put("conn_id", connId)
            put("zustand", "hintergrund")
        }.toString())

        val mailboxen = laufAblage(this).getString(KEY_MAILBOXEN, "[]") ?: "[]"
        val liste = runCatching { JSONArray(mailboxen) }.getOrNull()
        if (liste != null && liste.length() > 0) {
            post(basis, ausweis, "/api/events/mailboxes", JSONObject().apply {
                put("conn_id", connId)
                put("eintraege", liste)
            }.toString())
        }
    }

    private fun post(basis: String, ausweis: String, pfad: String, koerper: String) {
        try {
            val conn = URL("$basis$pfad").openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.setRequestProperty("Authorization", "Bearer $ausweis")
            conn.setRequestProperty("Content-Type", "application/json")
            conn.doOutput = true
            conn.connectTimeout = 10_000
            conn.readTimeout = 15_000
            conn.outputStream.use { it.write(koerper.toByteArray(Charsets.UTF_8)) }
            if (conn.responseCode !in 200..299) {
                Log.w(TAG, "$pfad antwortete ${conn.responseCode}")
            }
            conn.disconnect()
        } catch (e: Exception) {
            Log.w(TAG, "$pfad nicht erreichbar: ${e.message}")
        }
    }

    // ── Meldung ────────────────────────────────────────────────────────────

    private fun verarbeiten(inhalt: String) {
        val json = runCatching { JSONObject(inhalt) }.getOrNull() ?: return
        if (json.optString("type") != "e2ee_blind_message") return
        if (json.optBoolean("is_control", false)) return
        // Der Server wählt den Zusteller. `false` heisst: ein anderes Fenster
        // oder Gerät zeigt die Nachricht bereits.
        if (!json.optBoolean("melden", true)) return

        val kennung = json.optString("ereignis", "")
        if (kennung.isNotEmpty() && schonGesehen(kennung)) return

        melden(
            "Neue Nachricht",
            "Du hast eine neue verschlüsselte Nachricht erhalten.",
        )
    }

    private fun schonGesehen(kennung: String): Boolean {
        val prefs = laufAblage(this)
        val roh = prefs.getString(KEY_GESEHEN, "[]") ?: "[]"
        val liste = runCatching { JSONArray(roh) }.getOrElse { JSONArray() }
        for (i in 0 until liste.length()) {
            if (liste.optString(i) == kennung) return true
        }
        val neu = JSONArray()
        val start = maxOf(0, liste.length() - GESEHEN_MAX + 1)
        for (i in start until liste.length()) neu.put(liste.optString(i))
        neu.put(kennung)
        prefs.edit().putString(KEY_GESEHEN, neu.toString()).apply()
        return false
    }

    private fun melden(titel: String, text: String) {
        val absicht = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val click = PendingIntent.getActivity(
            this, 0, absicht,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val meldung = NotificationCompat.Builder(this, KANAL_MELDUNG)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(titel)
            .setContentText(text)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setAutoCancel(true)
            .setContentIntent(click)
            .build()
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify((System.currentTimeMillis() % 100_000).toInt(), meldung)
    }

    private fun statusMeldung(): Notification {
        val absicht = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val click = PendingIntent.getActivity(
            this, 0, absicht,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        // IMPORTANCE_LOW: keine Tonfolge für eine Dauerzeile, die ohnehin
        // immer da ist.
        return NotificationCompat.Builder(this, KANAL_STATUS)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Benachrichtigungen aktiv")
            .setContentText("Die App hält die Verbindung offen.")
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setContentIntent(click)
            .build()
    }

    private fun kanaeleAnlegen() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(
            NotificationChannel(
                KANAL_STATUS,
                "Hintergrunddienst",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Hält die Verbindung für Benachrichtigungen offen."
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                KANAL_MELDUNG,
                "Nachrichten",
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = "Neue Nachrichten aus dem Messenger"
                setShowBadge(true)
                enableVibration(true)
            },
        )
    }

    /**
     * 401/403 oder ein `shutdown`: der Ausweis gilt nicht mehr.
     *
     * Kein stiller Weiterlauf: der Schalter geht aus, damit ein Neustart nicht
     * endlos gegen dieselbe Wand läuft, und der Mensch erfährt, dass er neu
     * einschalten muss.
     *
     * Der eine Wert, den dieser Prozess in die Ablage der Oberfläche schreibt.
     * Er tut es einmal, im Ausnahmefall — ein gleichzeitiger Schreibvorgang
     * dort wäre schlimmer als ein Schalter, der einen Neustart zu früh zeigt.
     */
    private fun ausweisAbgelaufen() {
        einstellungen(this).edit().putBoolean(KEY_AKTIV, false).apply()
        laufAblage(this).edit().remove(KEY_AUSWEIS).remove(KEY_BASIS).apply()
        melden(
            "Benachrichtigungen angehalten",
            "Bitte im Profil neu einschalten — die Anmeldung wurde beendet.",
        )
        stopSelf()
    }

}
