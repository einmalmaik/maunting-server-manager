package com.mauntingstudios.smart_system

import org.json.JSONException
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.security.KeyStoreException
import java.security.ProviderException
import javax.crypto.AEADBadTagException

/**
 * Der Stand der Kamera-Sicherung zwischen zwei Läufen und wann ein Auftrag
 * verloren ist. Der Stand liegt als JSON auf dem Telefon und muss auch lesbar
 * bleiben, wenn eine neuere App Felder dazubekommt.
 */
class KameraAblageTest {
    private fun stand() = KameraAblage.Stand(
        kennung = "k1",
        konto = 4,
        server = "https://panel.example",
        bucket = "b".repeat(64),
        geraet = "0d6e3c1a-5b2f-4c8e-9a7d-1f2e3d4c5b6a",
        zugang = "verschluesselt",
        eingangId = "11111111-2222-4333-8444-555555555555",
        pq = "cHE=",
        rsa = "cnNh",
        nurWlan = true,
        screenshots = true,
        screenshotsAb = 156,
        marke = 185,
        markeId = 1_000_000_037,
        fassung = "gen:1409:abc",
        gesichert = 12,
        zuletzt = 1_791_048_619_988,
        warten = "wlan",
    )

    @Test
    fun `kommt aus dem JSON so zurueck, wie er hineinging`() {
        val s = stand()
        assertEquals(s, KameraAblage.Stand.aus(JSONObject(s.json().toString())))
        val ohneWarten = s.copy(warten = null)
        assertEquals(ohneWarten, KameraAblage.Stand.aus(JSONObject(ohneWarten.json().toString())))
    }

    @Test
    fun `liest einen Stand von vor dem Screenshot-Schalter`() {
        val j = stand().json().apply {
            remove("screenshots")
            remove("screenshotsAb")
            remove("markeId")
        }
        val s = KameraAblage.Stand.aus(JSONObject(j.toString()))
        assertFalse(s.screenshots)
        assertEquals(0L, s.screenshotsAb)
        // Ohne Kennung zur Marke ist alles mit dieser Marke durch, nichts wird doppelt angefangen.
        assertEquals(KameraAblage.ALLE, s.markeId)
        assertEquals(185L, s.marke)
    }

    @Test
    fun `nur ein Auftrag, der sich nicht mehr oeffnen laesst, ist verloren`() {
        assertTrue(KameraAblage.unrettbar(AEADBadTagException()))
        assertTrue(KameraAblage.unrettbar(JSONException("kaputt")))
        // Vorübergehend: der Lauf hört auf, der Auftrag bleibt für den nächsten.
        assertFalse(KameraAblage.unrettbar(KeyStoreException()))
        assertFalse(KameraAblage.unrettbar(ProviderException()))
        assertFalse(KameraAblage.unrettbar(IOException()))
    }
}
