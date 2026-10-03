package com.mauntingstudios.smart_system

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject

/**
 * Terminerinnerungen, die Android zur geplanten Zeit zeigt, auch bei
 * geschlossener App.
 *
 * Geplant wird nur aus der offenen App (`ErinnerungPlugin`), die Titel und
 * Zeiten aus ihrem entschlüsselten Kalender rechnet. Hier gibt es kein Netz und
 * kein Token. Die Liste liegt im privaten Speicher der App, damit der Wecker
 * nur einen Schlüssel trägt und ein Neustart des Geräts neu planen kann.
 * Gezeigte Schlüssel werden gemerkt, sonst käme eine sofort fällige
 * Erinnerung bei jedem neuen Plan noch einmal.
 */
object Erinnerungen {
    const val KANAL = "mss_alerts"
    private const val ABLAGE = "msm_erinnerungen"
    private const val LISTE = "liste"
    private const val GEZEIGT = "gezeigt"
    private const val GEZEIGT_HOECHSTENS = 300
    private const val AKTION = "com.mauntingstudios.smart_system.ERINNERUNG"

    data class Erinnerung(val schluessel: String, val zeit: Long, val titel: String, val text: String, val oeffentlich: String)

    private fun ablage(context: Context) = context.getSharedPreferences(ABLAGE, Context.MODE_PRIVATE)

    private fun lesen(json: String?): List<Erinnerung> {
        if (json.isNullOrEmpty()) return emptyList()
        return try {
            val liste = JSONArray(json)
            (0 until liste.length()).map { i ->
                val o = liste.getJSONObject(i)
                Erinnerung(o.getString("schluessel"), o.getLong("zeit"), o.getString("titel"), o.getString("text"), o.getString("oeffentlich"))
            }
        } catch (e: Exception) {
            emptyList()
        }
    }

    private fun schreiben(liste: List<Erinnerung>): String {
        val json = JSONArray()
        for (e in liste) {
            json.put(
                JSONObject()
                    .put("schluessel", e.schluessel)
                    .put("zeit", e.zeit)
                    .put("titel", e.titel)
                    .put("text", e.text)
                    .put("oeffentlich", e.oeffentlich)
            )
        }
        return json.toString()
    }

    private fun gezeigt(context: Context): List<String> = lesenText(ablage(context).getString(GEZEIGT, null))

    private fun lesenText(json: String?): List<String> = try {
        val a = JSONArray(json ?: "[]")
        (0 until a.length()).map { a.getString(it) }
    } catch (e: Exception) {
        emptyList()
    }

    private fun wecker(context: Context, schluessel: String): PendingIntent =
        PendingIntent.getBroadcast(
            context,
            schluessel.hashCode(),
            Intent(context, ErinnerungEmpfaenger::class.java).setAction(AKTION).putExtra("schluessel", schluessel),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    /** Ersetzt den ganzen Plan. Eine leere Liste nimmt alles weg, auch das Gedächtnis. */
    fun planen(context: Context, neu: List<Erinnerung>) {
        val alarme = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        for (alt in lesen(ablage(context).getString(LISTE, null))) {
            alarme.cancel(wecker(context, alt.schluessel))
        }
        val schonGezeigt = if (neu.isEmpty()) emptySet() else gezeigt(context).toSet()
        val offen = neu.filter { it.schluessel !in schonGezeigt }
        val editor = ablage(context).edit().putString(LISTE, schreiben(offen))
        if (neu.isEmpty()) editor.remove(GEZEIGT)
        editor.apply()
        stellen(context, alarme, offen)
    }

    /** Nach einem Neustart des Geräts sind alle Wecker weg. */
    fun neuStellen(context: Context) {
        val alarme = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        stellen(context, alarme, lesen(ablage(context).getString(LISTE, null)))
    }

    private fun stellen(context: Context, alarme: AlarmManager, liste: List<Erinnerung>) {
        val jetzt = System.currentTimeMillis()
        for (e in liste) {
            // Ungenau, aber auch im Ruhezustand: Android lässt sich bis zu
            // einer Stunde Spielraum (im Emulator gemessen). Für eine Erinnerung
            // 25 Stunden vorher reicht das, und die App braucht keine Erlaubnis
            // für exakte Wecker.
            alarme.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, maxOf(e.zeit, jetzt), wecker(context, e.schluessel))
        }
    }

    /** Vom Wecker gerufen: zeigen, aus dem Plan nehmen, als gezeigt merken. */
    fun zeigen(context: Context, schluessel: String) {
        val ablage = ablage(context)
        val liste = lesen(ablage.getString(LISTE, null))
        val e = liste.firstOrNull { it.schluessel == schluessel } ?: return
        val gezeigt = (gezeigt(context) + schluessel).takeLast(GEZEIGT_HOECHSTENS)
        ablage.edit()
            .putString(LISTE, schreiben(liste.filter { it.schluessel != schluessel }))
            .putString(GEZEIGT, JSONArray(gezeigt).toString())
            .apply()

        kanalAnlegen(context)
        val oeffnen = PendingIntent.getActivity(
            context,
            schluessel.hashCode(),
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // Auf dem Sperrbildschirm steht nur, dass ein Termin ansteht.
        val oeffentlich = NotificationCompat.Builder(context, KANAL)
            .setSmallIcon(R.mipmap.ic_launcher_foreground)
            .setContentTitle(e.oeffentlich)
            .build()
        val meldung = NotificationCompat.Builder(context, KANAL)
            .setSmallIcon(R.mipmap.ic_launcher_foreground)
            .setContentTitle(e.titel)
            .setContentText(e.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(e.text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setPublicVersion(oeffentlich)
            .setAutoCancel(true)
            .setContentIntent(oeffnen)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(schluessel.hashCode(), meldung)
        } catch (e: SecurityException) {
            // Ohne Erlaubnis für Benachrichtigungen zeigt Android nichts.
        }
    }

    /** Der Kanal entsteht sonst erst in `MainActivity`; ein Wecker kann vorher kommen. */
    fun kanalAnlegen(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(KANAL) != null) return
        nm.createNotificationChannel(
            NotificationChannel(KANAL, "Benachrichtigungen & Alarme", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Erinnerungen für Termine und Systemmeldungen"
                lockscreenVisibility = Notification.VISIBILITY_PRIVATE
            }
        )
    }
}

class ErinnerungEmpfaenger : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val schluessel = intent.getStringExtra("schluessel") ?: return
        Erinnerungen.zeigen(context, schluessel)
    }
}
