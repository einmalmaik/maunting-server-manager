package com.mauntingstudios.smart_system

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import java.sql.Connection
import java.sql.DriverManager

/**
 * Welche Aufnahmen die Kamera-Sicherung findet, geprüft an echtem SQLite: der
 * MediaStore ist SQLite, und `Medien.auswahl` ist die WHERE-Klausel, die er
 * bekommt. Zwei Fehler hingen schon an dieser Klausel: die Marke war die
 * Kennung statt der Generation (AGENTS.md Punkt 99), und eingeschaltete
 * Screenshots kamen rückwirkend mit (Punkt 108). Dasselbe gilt für die
 * Ordner, die nach der Kamera dazukamen (Messenger, Downloads).
 */
class MedienAuswahlTest {
    private lateinit var db: Connection

    @Before
    fun anlegen() {
        db = DriverManager.getConnection("jdbc:sqlite::memory:")
        db.createStatement().use {
            it.execute("CREATE TABLE files (_id INTEGER, generation_modified INTEGER, relative_path TEXT, _data TEXT, is_pending INTEGER)")
        }
    }

    @After
    fun schliessen() = db.close()

    private fun zeile(id: Long, generation: Long, pfad: String, wartend: Boolean = false) {
        db.prepareStatement("INSERT INTO files VALUES (?, ?, ?, ?, ?)").use {
            it.setLong(1, id)
            it.setLong(2, generation)
            it.setString(3, pfad)
            it.setString(4, "/storage/emulated/0/$pfad$id.jpg")
            it.setInt(5, if (wartend) 1 else 0)
            it.execute()
        }
    }

    private fun gefunden(sdk: Int, nach: Long, nachId: Long, weitereAb: Long, screenshotsAb: Long?, nurId: Long? = null): List<Long> {
        val (auswahl, werte) = Medien.auswahl(sdk, nach, nachId, weitereAb, screenshotsAb, nurId)
        db.prepareStatement("SELECT _id FROM files WHERE $auswahl ORDER BY ${Medien.reihenfolge(sdk)}").use { abfrage ->
            werte.forEachIndexed { i, wert -> abfrage.setString(i + 1, wert) }
            abfrage.executeQuery().use { r ->
                val ids = mutableListOf<Long>()
                while (r.next()) ids.add(r.getLong(1))
                return ids
            }
        }
    }

    /** Wie auf einem Telefon: Kamera, Screenshots an zwei Orten, Messenger, Downloads, Sticker. */
    private fun telefon() {
        zeile(1, 10, "DCIM/Camera/")
        zeile(2, 10, "DCIM/Camera/") // dieselbe Generation: eine Transaktion des MediaStore
        zeile(3, 10, "DCIM/Camera/")
        zeile(4, 11, "DCIM/Screenshots/")
        zeile(5, 12, "Pictures/Screenshots/")
        zeile(6, 13, "Pictures/")
        zeile(7, 14, "Pictures/Telegram/")
        zeile(8, 15, "DCIM/Camera/", wartend = true) // Video läuft noch
        zeile(9, 16, "Download/")
        zeile(10, 17, "DCIM/Screenshots/")
        zeile(11, 18, "Pictures/Screenshots/")
        zeile(12, 19, "DCIM/OpenCamera/")
        zeile(13, 20, "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Stickers/")
        zeile(14, 21, "Android/media/com.whatsapp/WhatsApp/Media/WhatsApp Images/")
    }

    /** Weitere Ordner noch nicht dabei: nur die Kamera. */
    private val nochNicht = Long.MAX_VALUE

    @Test
    fun `alle Fotos und Videos ausser Screenshots, Stickern und wartenden Aufnahmen`() {
        telefon()
        assertEquals(listOf(1L, 2, 3, 6, 7, 9, 12, 14), gefunden(34, 0, KameraAblage.ALLE, 0, null))
    }

    @Test
    fun `weitere Ordner erst ab ihrer Marke, die Kamera immer`() {
        telefon()
        // Mit dem Update bei Generation 13 dazugekommen: 6 entstand vorher.
        assertEquals(listOf(1L, 2, 3, 7, 9, 12, 14), gefunden(34, 0, KameraAblage.ALLE, 13, null))
        assertEquals(listOf(1L, 2, 3, 12), gefunden(34, 0, KameraAblage.ALLE, nochNicht, null))
    }

    @Test
    fun `setzt bei gleicher Generation an der Kennung fort`() {
        telefon()
        assertEquals(listOf(3L, 12), gefunden(34, 10, 2, nochNicht, null))
        assertEquals(listOf(12L), gefunden(34, 10, KameraAblage.ALLE, nochNicht, null))
        assertEquals(listOf(3L, 6, 7, 9, 12, 14), gefunden(34, 10, 2, 0, null))
    }

    @Test
    fun `Screenshots erst ab dem Einschalten, nicht rueckwirkend`() {
        telefon()
        // Eingeschaltet bei Generation 12: 4 und 5 entstanden vorher.
        assertEquals(listOf(1L, 2, 3, 10, 11, 12), gefunden(34, 0, KameraAblage.ALLE, nochNicht, 12))
    }

    @Test
    fun `Vorhandene sichern nimmt alles ausser Stickern`() {
        telefon()
        assertEquals(listOf(1L, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 14), gefunden(34, 0, KameraAblage.ALLE, 0, 0))
    }

    @Test
    fun `eine einzelne Aufnahme eines zugelassenen Auftrags, aus jedem Ordner ausser Stickern`() {
        telefon()
        // So fragt `Medien.aufnahme`: der Ordner wird nicht noch einmal gefiltert.
        assertEquals(listOf(4L), gefunden(34, 0, KameraAblage.ALLE, 0, 0, nurId = 4))
        assertEquals(listOf(7L), gefunden(34, 0, KameraAblage.ALLE, 0, 0, nurId = 7))
        assertEquals(emptyList<Long>(), gefunden(34, 0, KameraAblage.ALLE, 0, 0, nurId = 13))
        assertEquals(emptyList<Long>(), gefunden(34, 0, KameraAblage.ALLE, 0, 0, nurId = 8))
    }

    @Test
    fun `vor Android 11 ist die Kennung die Marke, vor Android 10 zaehlt der Pfad in _data`() {
        telefon()
        // Android 10: Marke ist _id, Pfad noch relative_path.
        assertEquals(listOf(3L, 12), gefunden(29, 2, KameraAblage.ALLE, nochNicht, null))
        // Android 9: kein relative_path, kein is_pending; die Wartende zählt hier mit.
        assertEquals(listOf(1L, 2, 3, 8, 12), gefunden(28, 0, KameraAblage.ALLE, nochNicht, null))
        assertEquals(listOf(8L, 10, 11, 12), gefunden(28, 5, KameraAblage.ALLE, nochNicht, 5))
        assertEquals(listOf(1L, 2, 3, 8, 9, 12, 14), gefunden(28, 0, KameraAblage.ALLE, 8, null))
    }
}
