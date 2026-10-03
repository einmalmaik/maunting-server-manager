package com.mauntingstudios.smart_system

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.File
import java.io.RandomAccessFile
import java.nio.file.Files
import java.util.UUID

/**
 * Die verschlüsselte Kopie einer geteilten Datei. Eine kaputte Kopie muss als
 * kaputt gelten: sonst setzte der Job den Auftrag bei jedem Lauf neu auf, und
 * jedes Mal entstünden neue Blobs beim Server. Dazu der Name, unter dem eine
 * Datei im Tresor steht.
 */
class KameraTeilenTest {
    private val ordner: File = Files.createTempDirectory("teilen").toFile()
    private val schluessel = ByteArray(32) { it.toByte() }
    private val inhalt = ByteArray(1000) { (it * 7).toByte() }

    private fun kopie(): File {
        val d = File(ordner, "${UUID.randomUUID()}.bin")
        KameraTeilen.verschluesseln(ByteArrayInputStream(inhalt), d, schluessel.copyOf(), ordner) {}
        return d
    }

    @Test
    fun `eine heile Kopie oeffnet sich`() {
        assertArrayEquals(inhalt, KameraTeilen.satz(kopie(), schluessel.copyOf(), 0))
    }

    @Test(expected = KameraTeilen.Kaputt::class)
    fun `ein veraendertes Byte macht die Kopie kaputt`() {
        val d = kopie()
        RandomAccessFile(d, "rw").use { it.seek(100); it.write(it.read() xor 1) }
        KameraTeilen.satz(d, schluessel.copyOf(), 0)
    }

    @Test(expected = KameraTeilen.Kaputt::class)
    fun `ein fehlender Satz macht die Kopie kaputt`() {
        KameraTeilen.satz(kopie(), schluessel.copyOf(), 1)
    }

    @Test
    fun `ein Name zeigt im Tresor, was er ist`() {
        // Aufnahmen und geteilte Dateien gehen durch dieselbe Bereinigung.
        assertEquals("Rechnung_gpj_.exe", KameraAuftrag.dateiname("Rechnung\u202Egpj\u200B.exe") { "x" })
        assertEquals("a_b_c.jpg", KameraAuftrag.dateiname("a/b\\c.jpg") { "x" })
        assertEquals("IMG_7", KameraAuftrag.dateiname("  ") { "IMG_7" })
        assertEquals(255, KameraAuftrag.dateiname("n".repeat(300)) { "x" }.length)
    }

    @Test(expected = KameraTeilen.Kaputt::class)
    fun `ein vertauschter Satz oeffnet sich nicht unter fremdem Namen`() {
        val d = kopie()
        val fremd = File(ordner, "${UUID.randomUUID()}.bin")
        d.copyTo(fremd)
        KameraTeilen.satz(fremd, schluessel.copyOf(), 0)
    }
}
