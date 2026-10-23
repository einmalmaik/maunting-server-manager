package com.mauntingstudios.secure_browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Externe Messenger bekommen getrennte persistente Profile. */
class PanelProfilTest {
    @Test
    fun `jeder Dienst hat sein eigenes Profil`() {
        val ids = listOf("tab-panel-whatsapp", "tab-panel-discord", "tab-panel-telegram", "tab-panel-instagram")
        val profile = ids.map { panelProfil(it) }
        assertEquals(listOf("aussen_whatsapp", "aussen_discord", "aussen_telegram", "aussen_instagram"), profile)
        assertEquals("Profile sind eindeutig", profile.size, profile.toSet().size)
        assertEquals(PANEL_PROFILE.toSet(), profile.toSet())
        assertNull(panelProfil("tab-a"))
        assertNull(panelProfil("tab-panel-youtube"))
    }
}
