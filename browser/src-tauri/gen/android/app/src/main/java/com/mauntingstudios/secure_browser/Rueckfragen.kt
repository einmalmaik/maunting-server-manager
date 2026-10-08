package com.mauntingstudios.secure_browser

import org.json.JSONObject

/**
 * Rückfragen einer Seite, die auf die Oberfläche warten (Dialog, Anmeldung,
 * Kontextmenü). Die Oberfläche antwortet über `nr` (`TabsPlugin.antworten`).
 * Schließt ein Tab, wird alles Offene abgebrochen, damit keine Seite hängt.
 * Nur auf dem UI-Faden.
 */
object Rueckfragen {
  private var zaehler = 0L
  private val offen = HashMap<Long, Pair<String, (JSONObject?) -> Unit>>()

  /** `antwort` bekommt `null`, wenn der Tab vorher schließt. */
  fun neu(tab: String, antwort: (JSONObject?) -> Unit): Long {
    zaehler += 1
    offen[zaehler] = tab to antwort
    return zaehler
  }

  fun antworten(nr: Long, antwort: JSONObject) {
    offen.remove(nr)?.second?.invoke(antwort)
  }

  fun tabWeg(tab: String) {
    val weg = offen.filterValues { it.first == tab }.keys
    for (nr in weg) offen.remove(nr)?.second?.invoke(null)
  }
}
