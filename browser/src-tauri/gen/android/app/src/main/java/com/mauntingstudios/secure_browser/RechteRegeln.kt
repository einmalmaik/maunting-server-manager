package com.mauntingstudios.secure_browser

import java.net.URI

/** Regeln für Seitenrechte, ohne Android-Abhängigkeit testbar. */
object RechteRegeln {
  /** Nur echte Web-Herkünfte ohne Zugangsdaten dürfen nach Kamera/Mikrofon fragen. */
  fun erlaubteHerkunft(roh: String?): Boolean {
    val uri = try {
      URI(roh)
    } catch (e: Exception) {
      return false
    }
    val schema = uri.scheme?.lowercase()
    return (schema == "http" || schema == "https") &&
      !uri.host.isNullOrEmpty() &&
      uri.userInfo == null
  }

  /** Alle angefragten Systemrechte müssen erteilt sein; eine leere Antwort zählt nicht. */
  fun alleErteilt(ergebnisse: Map<String, Boolean>): Boolean =
    ergebnisse.isNotEmpty() && ergebnisse.values.all { it }
}
