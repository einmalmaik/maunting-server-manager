package com.mauntingstudios.secure_browser

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RechteRegelnTest {
  @Test
  fun nimmt_nur_webseiten_ohne_zugangsdaten() {
    assertTrue(RechteRegeln.erlaubteHerkunft("https://example.org"))
    assertTrue(RechteRegeln.erlaubteHerkunft("http://127.0.0.1:8080"))
    for (schlecht in listOf(
      null,
      "",
      "file:///C:/a.html",
      "data:text/html,x",
      "javascript:alert(1)",
      "https://benutzer:geheim@example.org/",
      "https://",
    )) {
      assertFalse(schlecht ?: "null", RechteRegeln.erlaubteHerkunft(schlecht))
    }
  }

  @Test
  fun verlangt_alle_angefragten_rechte() {
    assertTrue(RechteRegeln.alleErteilt(mapOf("a" to true, "b" to true)))
    assertFalse(RechteRegeln.alleErteilt(mapOf("a" to true, "b" to false)))
    assertFalse(RechteRegeln.alleErteilt(emptyMap()))
  }
}
