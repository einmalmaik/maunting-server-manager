package com.mauntingstudios.secure_browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Die Kennungen sind die echten der Emulatoren (Android 12 und 15). */
class WebviewPruefungTest {
    private val android12 = "Mozilla/5.0 (Linux; Android 12; sdk_gphone64_x86_64 Build/SE1A.220826.008; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36"
    private val android15 = "Mozilla/5.0 (Linux; Android 15; sdk_gphone64_x86_64 Build/AE3A.240806.043; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.219 Mobile Safari/537.36"

    @Test
    fun `liest die Fassung aus der Kennung`() {
        assertEquals(91, WebviewPruefung.chromeVersion(android12))
        assertEquals(124, WebviewPruefung.chromeVersion(android15))
    }

    @Test
    fun `zu alt ist eine alte Fassung oder eine fehlende Funktion`() {
        assertTrue(WebviewPruefung.zuAlt(android12, funktionen = true))
        assertFalse(WebviewPruefung.zuAlt(android15, funktionen = true))
        assertTrue("ohne eigene Profile teilen Seiten den Speicher der App", WebviewPruefung.zuAlt(android15, funktionen = false))
        assertFalse(WebviewPruefung.zuAlt("eigene WebView", funktionen = true))
    }
}
