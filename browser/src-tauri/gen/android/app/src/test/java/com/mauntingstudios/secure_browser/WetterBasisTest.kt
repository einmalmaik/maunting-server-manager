package com.mauntingstudios.secure_browser

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Die Wetter-Adresse darf kein getarntes Loopback und kein Fremdschema sein. */
class WetterBasisTest {
    @Test
    fun `erlaubt TLS mit Host`() {
        assertTrue(wetterBasisErlaubt("https://panel.example.com"))
        assertTrue(wetterBasisErlaubt("https://panel.example.com:8443/api"))
        assertTrue(wetterBasisErlaubt("HTTPS://panel.example.com"))
        assertTrue(wetterBasisErlaubt(" https://panel.example.com/ "))
    }

    @Test
    fun `erlaubt unverschluesselt nur echtes Loopback`() {
        assertTrue(wetterBasisErlaubt("http://127.0.0.1:8000"))
        assertTrue(wetterBasisErlaubt("http://localhost:8000"))
        assertTrue(wetterBasisErlaubt("http://LOCALHOST:8000"))
        assertTrue(wetterBasisErlaubt("http://[::1]:8000"))
    }

    @Test
    fun `lehnt getarnte Loopback-Namen ab`() {
        assertFalse(wetterBasisErlaubt("http://localhost.evil.example"))
        assertFalse(wetterBasisErlaubt("http://127.0.0.1.evil.example"))
        assertFalse(wetterBasisErlaubt("http://127.0.0.1@evil.example"))
        assertFalse(wetterBasisErlaubt("http://evil.example"))
        assertFalse(wetterBasisErlaubt("http://localhost."))
    }

    @Test
    fun `lehnt fremde Schemata und Kaputtes ab`() {
        assertFalse(wetterBasisErlaubt("ftp://panel.example.com"))
        assertFalse(wetterBasisErlaubt("javascript:alert(1)"))
        assertFalse(wetterBasisErlaubt("https://"))
        assertFalse(wetterBasisErlaubt(""))
        assertFalse(wetterBasisErlaubt("   "))
    }
}
