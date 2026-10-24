package com.mauntingstudios.secure_browser

import java.net.URI

/**
 * Ist die gespeicherte MSM-Adresse für Wetter und Widget-Token zulässig?
 *
 * Nur TLS oder ausdrücklich Loopback. Ein Präfixvergleich reichte nicht:
 * `http://localhost.evil.example` beginnt ebenfalls mit `http://localhost`.
 */
internal fun wetterBasisErlaubt(basis: String): Boolean {
  val url = runCatching { URI(basis.trim()) }.getOrNull() ?: return false
  return when (url.scheme?.lowercase()) {
    "https" -> !url.host.isNullOrBlank()
    "http" -> {
      val host = url.host?.lowercase() ?: return false
      host in setOf("127.0.0.1", "localhost", "::1", "[::1]")
    }
    else -> false
  }
}
