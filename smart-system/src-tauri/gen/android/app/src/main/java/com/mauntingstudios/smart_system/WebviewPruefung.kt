package com.mauntingstudios.smart_system

/**
 * Ob die WebView dieses Telefons die Oberfläche tragen kann.
 *
 * Das Frontend braucht Sprachmittel ab Chrome 80 und Funktionen, die erst
 * später kamen. Unter Android 10 mit nie aktualisierter WebView (Chrome 74)
 * blieb die App eine weiße Seite ohne Erklärung (03.10.2026, Emulator). Die
 * Grenze ist die älteste Fassung, auf der alles geprüft lief: Android 12 mit
 * WebView 91. Was darunter liegt, bekommt einen Hinweis statt der leeren Seite.
 */
object WebviewPruefung {
    const val MINDEST = 91

    fun chromeVersion(userAgent: String): Int? =
        Regex("Chrome/(\\d+)\\.").find(userAgent)?.groupValues?.get(1)?.toIntOrNull()

    /** Ohne erkennbare Fassung wird nichts gesperrt: dann versucht es die Oberfläche selbst. */
    fun zuAlt(userAgent: String): Boolean = chromeVersion(userAgent)?.let { it < MINDEST } ?: false
}
