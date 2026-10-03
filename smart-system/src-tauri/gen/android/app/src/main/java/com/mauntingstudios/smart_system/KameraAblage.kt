package com.mauntingstudios.smart_system

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Was die Kamera-Sicherung zwischen zwei Läufen weiß. Gibt es keinen Stand,
 * ist sie aus.
 *
 * Liegt in `noBackupFilesDir`: eine Sicherung des Telefons nimmt nichts davon
 * mit. Zugang und Aufträge (rohe Blob-Schlüssel, Vorschaubilder) liegen
 * verschlüsselt mit einem Schlüssel im Android Keystore, der ohne Abfrage
 * gilt: der Job läuft bei gesperrtem Telefon.
 *
 * `kennung` wechselt mit jedem Einrichten. Ein Lauf schreibt nur, solange sie
 * gleich ist (AGENTS.md Punkt 45): nach Abmelden oder Kontowechsel legt ein
 * noch laufender Job nichts mehr an.
 */
object KameraAblage {
    data class Stand(
        val kennung: String,
        val konto: Long,
        val server: String,
        val bucket: String,
        val geraet: String,
        /** Zugang zum Server, verschlüsselt (`zu`/`auf`). */
        val zugang: String,
        val eingangId: String,
        /** Öffentlicher Schlüssel des Posteingangs: ML-KEM-768 roh, RSA als SPKI, beides Base64. */
        val pq: String,
        val rsa: String,
        val nurWlan: Boolean,
        /** Auch Bildschirmfotos sichern (`Pictures/Screenshots`, `DCIM/Screenshots`). */
        val screenshots: Boolean = false,
        /**
         * Bildschirmfotos zählen erst ab dieser Marke: ab dem Einschalten, nicht
         * rückwirkend bis zur letzten Kameraaufnahme. 0 bei „Vorhandene sichern“.
         */
        val screenshotsAb: Long = 0,
        /**
         * Alle Aufnahmen bis zu dieser Stelle sind in einem Auftrag oder erledigt:
         * Marke, und bei gleicher Marke die Kennung (`markeId`). Viele Aufnahmen
         * können dieselbe Generation tragen (eine Transaktion des MediaStore).
         * `ALLE` heißt: alles mit dieser Marke ist durch.
         */
        val marke: Long,
        val markeId: Long = ALLE,
        val fassung: String,
        val gesichert: Int = 0,
        val zuletzt: Long = 0,
        /** Warum gerade nichts gesichert wird: zugriff, wlan, zugang, speicher, fehler. */
        val warten: String? = null,
    ) {
        fun json(): JSONObject = JSONObject().apply {
            put("kennung", kennung); put("konto", konto); put("server", server); put("bucket", bucket)
            put("geraet", geraet); put("zugang", zugang); put("eingangId", eingangId); put("pq", pq); put("rsa", rsa)
            put("nurWlan", nurWlan); put("screenshots", screenshots); put("screenshotsAb", screenshotsAb); put("marke", marke); put("markeId", markeId); put("fassung", fassung)
            put("gesichert", gesichert); put("zuletzt", zuletzt); put("warten", warten ?: JSONObject.NULL)
        }

        companion object {
            fun aus(j: JSONObject) = Stand(
                kennung = j.getString("kennung"), konto = j.getLong("konto"), server = j.getString("server"),
                bucket = j.getString("bucket"), geraet = j.getString("geraet"), zugang = j.getString("zugang"),
                eingangId = j.getString("eingangId"), pq = j.getString("pq"), rsa = j.getString("rsa"),
                nurWlan = j.getBoolean("nurWlan"), screenshots = j.optBoolean("screenshots"), screenshotsAb = j.optLong("screenshotsAb"), marke = j.getLong("marke"), markeId = j.optLong("markeId", ALLE),
                fassung = j.getString("fassung"), gesichert = j.optInt("gesichert"), zuletzt = j.optLong("zuletzt"),
                warten = if (j.isNull("warten")) null else j.optString("warten"),
            )
        }
    }

    /** `markeId` für „alles mit dieser Marke ist durch“. */
    const val ALLE = Long.MAX_VALUE

    private const val SCHLUESSEL = "msm-kamera-ablage"
    private val sperre = Any()

    private fun ordner(ctx: Context): File = File(ctx.noBackupFilesDir, "kamera").apply { mkdirs() }
    private fun standDatei(ctx: Context) = File(ordner(ctx), "stand.json")
    private fun auftragOrdner(ctx: Context): File = File(ordner(ctx), "auftraege").apply { mkdirs() }
    private fun bekanntDatei(ctx: Context) = File(ordner(ctx), "bekannt.txt")

    private fun schluessel(): SecretKey {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (ks.getEntry(SCHLUESSEL, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        val erzeuger = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        erzeuger.init(
            KeyGenParameterSpec.Builder(SCHLUESSEL, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build(),
        )
        return erzeuger.generateKey()
    }

    /** Nullt `daten` danach, auf jedem Weg: Aufrufer geben nur frische Kopien hinein. */
    private fun verschluesseln(daten: ByteArray): ByteArray {
        try {
            val c = Cipher.getInstance("AES/GCM/NoPadding")
            c.init(Cipher.ENCRYPT_MODE, schluessel())
            return c.iv + c.doFinal(daten)
        } finally {
            daten.fill(0)
        }
    }

    /** Entschlüsselt zu Text und nullt die Bytes dazwischen. Der Text selbst lässt sich nicht nullen. */
    private fun textAus(daten: ByteArray): String {
        val klar = entschluesseln(daten)
        try {
            return String(klar)
        } finally {
            klar.fill(0)
        }
    }

    private fun entschluesseln(daten: ByteArray): ByteArray {
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, schluessel(), GCMParameterSpec(128, daten, 0, 12))
        return c.doFinal(daten, 12, daten.size - 12)
    }

    fun zu(text: String): String = Base64.encodeToString(verschluesseln(text.toByteArray()), Base64.NO_WRAP)
    fun auf(text: String): String = textAus(Base64.decode(text, Base64.NO_WRAP))

    /** Schreibt neben das Ziel und benennt um: ein Abbruch lässt keine halbe Datei. */
    private fun ablegen(ziel: File, daten: ByteArray) {
        val neu = File(ziel.parentFile, ziel.name + ".neu")
        neu.writeBytes(daten)
        if (!neu.renameTo(ziel)) {
            neu.delete()
            throw IllegalStateException("Ablage nicht beschreibbar")
        }
    }

    fun lesen(ctx: Context): Stand? = synchronized(sperre) {
        val datei = standDatei(ctx)
        if (!datei.exists()) return null
        try {
            Stand.aus(JSONObject(datei.readText()))
        } catch (e: Exception) {
            null
        }
    }

    /** Ersetzt den Stand ganz (Einrichten). */
    fun setzen(ctx: Context, stand: Stand) = synchronized(sperre) {
        ablegen(standDatei(ctx), stand.json().toString().toByteArray())
    }

    /** Ändert den Stand, wenn er noch zu `kennung` gehört; sonst `null` und nichts geschrieben. */
    fun aendern(ctx: Context, kennung: String, f: (Stand) -> Stand): Stand? = synchronized(sperre) {
        val alt = lesen(ctx) ?: return null
        if (alt.kennung != kennung) return null
        val neu = f(alt)
        ablegen(standDatei(ctx), neu.json().toString().toByteArray())
        neu
    }

    /**
     * Abmelden, Kontowechsel, Ausschalten: alles weg. Laufende Aufträge
     * verwaisen; der Server räumt offene Uploads ab. Mit `kennung` nur, wenn
     * der Stand noch dazu gehört: ein alter Lauf löscht keine neue Einrichtung.
     */
    fun vergessen(ctx: Context, kennung: String? = null): Boolean = synchronized(sperre) {
        if (kennung != null && lesen(ctx)?.kennung != kennung) return false
        ordner(ctx).deleteRecursively()
        true
    }

    /**
     * Ob ein Auftrag mit diesem Fehler verloren ist: mit dem heutigen Schlüssel
     * nicht zu öffnen (Keystore neu, Datei kaputt) oder kein JSON. Alles andere
     * (Keystore gerade nicht erreichbar, Lesefehler) beendet nur den Lauf, und
     * der Auftrag bleibt: die Marke steht schon hinter seiner Aufnahme, gelöscht
     * wäre sie nie gesichert.
     */
    fun unrettbar(e: Exception): Boolean = e is AEADBadTagException || e is JSONException

    fun auftraege(ctx: Context, kennung: String): List<JSONObject> = synchronized(sperre) {
        if (lesen(ctx)?.kennung != kennung) return emptyList()
        auftragOrdner(ctx).listFiles { f -> f.name.endsWith(".auftrag") }.orEmpty().sortedBy { it.lastModified() }.mapNotNull { f ->
            try {
                JSONObject(textAus(f.readBytes()))
            } catch (e: Exception) {
                if (!unrettbar(e)) throw e
                f.delete()
                null
            }
        }
    }

    /** Legt einen Auftrag ab, nur solange der Stand noch zu `kennung` gehört. */
    fun auftragSchreiben(ctx: Context, kennung: String, auftrag: JSONObject): Boolean = synchronized(sperre) {
        if (lesen(ctx)?.kennung != kennung) return false
        ablegen(File(auftragOrdner(ctx), auftrag.getString("id") + ".auftrag"), verschluesseln(auftrag.toString().toByteArray()))
        true
    }

    fun auftragEntfernen(ctx: Context, id: String) = synchronized(sperre) {
        File(auftragOrdner(ctx), "$id.auftrag").delete()
    }

    fun anzahlAuftraege(ctx: Context): Int = synchronized(sperre) {
        auftragOrdner(ctx).listFiles { f -> f.name.endsWith(".auftrag") }?.size ?: 0
    }

    /** Was schon im Tresor liegt, als `medienId:sha256`. */
    fun bekannt(ctx: Context): MutableSet<String> = synchronized(sperre) {
        val datei = bekanntDatei(ctx)
        if (datei.exists()) datei.readLines().filter { it.isNotBlank() }.toMutableSet() else mutableSetOf()
    }

    fun bekanntDazu(ctx: Context, kennung: String, eintraege: Collection<String>) = synchronized(sperre) {
        if (eintraege.isEmpty() || lesen(ctx)?.kennung != kennung) return
        bekanntDatei(ctx).appendText(eintraege.joinToString("\n", postfix = "\n"))
    }
}
