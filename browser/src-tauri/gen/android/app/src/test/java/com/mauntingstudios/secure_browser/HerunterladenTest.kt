package com.mauntingstudios.secure_browser

import org.junit.Assert.assertEquals
import org.junit.Test

class HerunterladenTest {
    @Test
    fun `ein zweiter gleichnamiger Download behält seine Endung`() {
        assertEquals("bericht (2).pdf", Herunterladen.mitNummer("bericht.pdf", 2))
        assertEquals("archiv.tar (3).gz", Herunterladen.mitNummer("archiv.tar.gz", 3))
        assertEquals("LIESMICH (2)", Herunterladen.mitNummer("LIESMICH", 2))
        assertEquals(".profil (2)", Herunterladen.mitNummer(".profil", 2))
    }
}
