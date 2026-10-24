package com.mauntingstudios.secure_browser

import android.content.Intent
import android.speech.RecognizerIntent

/**
 * Der gemeinsame Weg zur System-Spracherkennung: das Widget und die beiden
 * Mikrofonknöpfe der Oberfläche öffnen dieselbe App, die auf dem Telefon
 * eingerichtet ist. Der Browser bekommt nur den fertigen Text.
 */
object SprachText {
  /** Mehr spricht niemand in eine Suche; der Rest fällt weg. */
  const val MAX = 2000

  fun intent(): Intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(
    RecognizerIntent.EXTRA_LANGUAGE_MODEL,
    RecognizerIntent.LANGUAGE_MODEL_WEB_SEARCH,
  )

  /** Der erste erkannte Text, getrimmt und gekürzt; `null` bei Abbruch oder leer. */
  fun aus(daten: Intent?): String? =
    kuerzen(daten?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull())

  fun kuerzen(text: String?): String? =
    text?.trim()?.takeIf { it.isNotEmpty() }?.take(MAX)
}
