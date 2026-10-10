package com.mauntingstudios.secure_browser

/**
 * Was die Tab-WebViews an Rust geben (`src/tabs/android/bruecke.rs`).
 *
 * Rust entscheidet mit denselben Funktionen wie unter Windows: ob eine
 * Navigation lädt, ob eine Anfrage geblockt wird, was eine Meldung von
 * `seite.js` bedeutet. Kotlin zeigt nur an. Adressen kommen immer aus der
 * WebView, nie aus einer Nachricht der Seite.
 *
 * Die Bibliothek lädt Tauri beim Start; die Namen hier sind die JNI-Symbole
 * `Java_com_mauntingstudios_secure_1browser_TabsBruecke_*` und dürfen sich
 * nur mit ihnen zusammen ändern (deshalb ohne R8, `build.gradle.kts`).
 */
object TabsBruecke {
  /** Ein Ereignis eines Tabs als JSON in der Form von `TabEreignis`. */
  @JvmStatic external fun melden(json: String)

  /**
   * `""` laden, `"-"` nicht laden, sonst die Adresse, die stattdessen lädt.
   * [weiterleitung]: der Server hat hierher umgeleitet.
   */
  @JvmStatic external fun weg(tab: String, url: String, weiterleitung: Boolean): String

  /** Nach einem Ladefehler die Adresse mit `http://`, wenn Rust sie hochgestuft hat, sonst `""`. */
  @JvmStatic external fun rueckfall(url: String): String

  /** `true`: blocken. Läuft auf einem Faden der WebView. */
  @JvmStatic external fun anfrage(tab: String, url: String, seite: String, hauptframe: Boolean, accept: String): Boolean

  @JvmStatic external fun seitenwechsel(tab: String)

  /** Eine Meldung von `seite.js` aus dem obersten Rahmen; `url` aus der WebView. */
  @JvmStatic external fun nachricht(tab: String, url: String, roh: String)

  /** Eine Meldung von `seite.js` aus einem Unterrahmen; gibt dessen Herkunft zurück, wenn er fürs Füllen in Frage kommt, sonst "". */
  @JvmStatic external fun rahmen(tab: String, url: String, absender: String, roh: String): String

  /** Steht ein Rahmen mit dieser Herkunft (`sourceOrigin`) auf einer gesperrten Seite? */
  @JvmStatic external fun rahmenGesperrt(herkunft: String): Boolean

  @JvmStatic external fun gleicheHerkunft(a: String, b: String): Boolean

  /** Der bereinigte Dateiname eines Downloads, leer, wenn die Adresse gesperrt ist. */
  @JvmStatic external fun dateiname(url: String, vorschlag: String): String

  /** Das Such-Widget hat etwas angestoßen; die Oberfläche holt es ab. */
  @JvmStatic external fun widget()

  @JvmStatic external fun seitenskript(): String

  /** Das Cookie-Skript mit dem heutigen Stand des Schilds (`cookies.rs`). */
  @JvmStatic external fun cookiesskript(): String

  /** Skript mit der Kosmetik dieser Seite, leer, wenn keine. */
  @JvmStatic external fun kosmetik(url: String): String

  /** Skript, das Klassen und IDs sammelt, leer, wenn die Seite das abschaltet. */
  @JvmStatic external fun klassenSammeln(url: String): String

  /** Skript mit den allgemeinen Regeln für die gesammelten Namen, leer, wenn keine. */
  @JvmStatic external fun allgemein(url: String, json: String): String
}
