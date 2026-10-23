package com.mauntingstudios.secure_browser

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SprachTextTest {
  @Test
  fun laesst_leere_und_kurze_texte_durch() {
    assertNull(SprachText.kuerzen(null))
    assertNull(SprachText.kuerzen("   "))
    assertEquals("Hallo Welt", SprachText.kuerzen("  Hallo Welt  "))
  }

  @Test
  fun kuerzt_lange_texte_auf_die_grenze() {
    val lang = "ä".repeat(SprachText.MAX + 20)
    assertEquals(SprachText.MAX, SprachText.kuerzen(lang)?.length)
  }
}
