package com.mauntingstudios.smart_system

import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.format.Formatter
import android.view.Gravity
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import kotlin.concurrent.thread

/**
 * „Tresor“ im Teilen-Menü anderer Apps. Legt jede geteilte Datei verschlüsselt
 * als Auftrag der Kamera-Sicherung ab (`KameraTeilen`) und stößt den Job an.
 * Der Tresor muss dafür nicht offen sein; im Tresor erscheint die Datei nach
 * dem nächsten Entsperren.
 *
 * Nur aktiv, solange die Kamera-Sicherung eingerichtet ist (`KameraTeilen.zielSetzen`).
 * Zurück ist gesperrt, bis alles gelesen ist: die Freigabe der anderen App gilt
 * nur, solange diese Activity lebt.
 */
class TeilenActivity : Activity() {
    @Volatile private var fertig = false

    private lateinit var text: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setFinishOnTouchOutside(false)
        if (savedInstanceState != null) {
            // Nach einem Neuaufbau (das System hat den Prozess beendet) gilt die Freigabe nicht mehr.
            finish()
            return
        }
        val stand = KameraAblage.lesen(this)
        if (stand == null) {
            beenden(getString(R.string.teilen_aus))
            return
        }
        val dateien = dateien(intent)
        if (dateien.isEmpty()) {
            beenden(getString(R.string.teilen_keine_datei))
            return
        }

        val abstand = (24 * resources.displayMetrics.density).toInt()
        text = TextView(this).apply { text = getString(R.string.teilen_laeuft) }
        setContentView(
            LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                gravity = Gravity.CENTER_HORIZONTAL
                setPadding(abstand, abstand, abstand, abstand)
                addView(ProgressBar(this@TeilenActivity, null, android.R.attr.progressBarStyleHorizontal).apply { isIndeterminate = true })
                addView(text)
            },
        )

        val typHinweis = intent.type
        thread(name = "msm-teilen") {
            var gut = 0
            var keinPlatz = false
            dateien.forEachIndexed { i, uri ->
                try {
                    val ok = KameraTeilen.aufnehmen(this, stand, uri, typHinweis) { bytes ->
                        zeigen(getString(R.string.teilen_fortschritt, i + 1, dateien.size, Formatter.formatShortFileSize(this, bytes)))
                    }
                    if (ok) gut++
                } catch (e: KameraTeilen.KeinPlatz) {
                    keinPlatz = true
                } catch (e: Exception) {
                    // Diese eine Datei; die übrigen gehen weiter.
                }
            }
            if (gut > 0) runCatching { KameraPlan.anstossen(applicationContext) }
            val meldung = when {
                gut == dateien.size -> resources.getQuantityString(R.plurals.teilen_fertig, gut, gut)
                keinPlatz -> getString(R.string.teilen_kein_platz)
                gut == 0 -> getString(R.string.teilen_fehler)
                else -> getString(R.string.teilen_teilweise, gut, dateien.size)
            }
            fertig = true
            runOnUiThread { beenden(meldung) }
        }
    }

    private fun zeigen(meldung: String) = runOnUiThread { text.text = meldung }

    private fun beenden(meldung: String) {
        Toast.makeText(applicationContext, meldung, Toast.LENGTH_LONG).show()
        finish()
    }

    @Deprecated("Zurück bleibt gesperrt, bis die Dateien gelesen sind")
    override fun onBackPressed() {
        if (fertig) {
            @Suppress("DEPRECATION")
            super.onBackPressed()
        }
    }

    private fun dateien(intent: Intent): List<Uri> {
        @Suppress("DEPRECATION")
        val liste = when (intent.action) {
            Intent.ACTION_SEND -> listOfNotNull(
                if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_STREAM, Uri::class.java) else intent.getParcelableExtra(Intent.EXTRA_STREAM),
            )
            Intent.ACTION_SEND_MULTIPLE ->
                (if (Build.VERSION.SDK_INT >= 33) intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM, Uri::class.java) else intent.getParcelableArrayListExtra(Intent.EXTRA_STREAM)).orEmpty()
            else -> emptyList()
        }
        // Nur Inhalte anderer Apps; eine Datei-Adresse oder ein eigener Provider wäre ein Weg, die Dateien dieser App zu lesen.
        return liste.filter { it.scheme == "content" && !it.authority.orEmpty().startsWith(packageName) }.distinct()
    }
}
