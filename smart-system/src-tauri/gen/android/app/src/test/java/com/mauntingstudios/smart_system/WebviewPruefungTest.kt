package com.mauntingstudios.smart_system

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Die Kennungen sind die echten der Emulatoren (Android 10, 12, 15). */
class WebviewPruefungTest {
    private val android10 = "Mozilla/5.0 (Linux; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/74.0.3729.185 Mobile Safari/537.36"
    private val android12 = "Mozilla/5.0 (Linux; Android 12; sdk_gphone64_x86_64 Build/SE1A.220826.008; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36"
    private val android15 = "Mozilla/5.0 (Linux; Android 15; sdk_gphone64_x86_64 Build/AE3A.240806.043; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.6367.219 Mobile Safari/537.36"

    @Test
    fun `liest die Fassung aus der Kennung`() {
        assertEquals(74, WebviewPruefung.chromeVersion(android10))
        assertEquals(91, WebviewPruefung.chromeVersion(android12))
        assertEquals(124, WebviewPruefung.chromeVersion(android15))
    }

    @Test
    fun `nur was unter der geprüften Fassung liegt, ist zu alt`() {
        assertTrue(WebviewPruefung.zuAlt(android10))
        assertFalse(WebviewPruefung.zuAlt(android12))
        assertFalse(WebviewPruefung.zuAlt(android15))
    }

    @Test
    fun `ohne erkennbare Fassung wird nichts gesperrt`() {
        assertNull(WebviewPruefung.chromeVersion("Mozilla/5.0 (Linux; Android 9) eigene WebView"))
        assertFalse(WebviewPruefung.zuAlt(""))
    }
}
