package com.mauntingstudios.smart_system

import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.DocumentsContract
import android.provider.OpenableColumns
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Dateien, die aus einer anderen App in den Tresor geteilt werden
 * (`TeilenActivity`). Sie gehen denselben Weg wie die Kamera-Sicherung: ein
 * Auftrag, den der Hintergrund-Job hochlädt, ohne Netz später und ohne dass
 * der Tresor offen sein muss.
 *
 * Die Freigabe der anderen App gilt nur, solange die Activity lebt. Deshalb
 * wird die Datei sofort gelesen und als verschlüsselte Kopie in den Ordner der
 * Kamera-Sicherung gelegt: je 4 MiB ein AES-GCM-Satz mit einem eigenen
 * Schlüssel, der nur im (Keystore-verschlüsselten) Auftrag steht. Klartext
 * liegt nie auf dem Telefon. Der Job liest Satz für Satz, so wie er bei einer
 * Aufnahme Stück für Stück aus dem MediaStore liest. Ist die Datei im Tresor,
 * fällt die Kopie weg.
 */
object KameraTeilen {
    class KeinPlatz : IOException()

    private const val GCM = 16
    private const val IV = 12
    private const val SATZ = KameraKrypto.CHUNK + IV + GCM

    /** Platz, der auf dem Telefon frei bleiben soll. */
    private const val RESERVE = 200L * 1024 * 1024

    /** Eine Kopie ohne Auftrag ist verwaist (abgebrochenes Teilen), wenn sie so lange nicht mehr geschrieben wurde. */
    private const val VERWAIST_MS = 60L * 60 * 1000

    private val DATEI = Regex("^[0-9a-f-]{36}\\.bin$")
    private val SCHLUESSEL = Regex("^[0-9a-f]{64}$")
    private val UNERWUENSCHT = Regex("[\\p{Cc}\\p{Cf}/\\\\]")

    private val zufall = SecureRandom()

    private fun ordner(ctx: Context): File = File(ctx.noBackupFilesDir, "kamera/geteilt").apply { mkdirs() }

    private fun datei(ctx: Context, auftrag: JSONObject): File {
        val name = auftrag.getJSONObject("lokal").getString("datei")
        require(DATEI.matches(name)) { "Ungültige Kopie" }
        return File(ordner(ctx), name)
    }

    private fun schluessel(auftrag: JSONObject): ByteArray {
        val hex = auftrag.getJSONObject("lokal").getString("schluessel")
        require(SCHLUESSEL.matches(hex)) { "Ungültiger Schlüssel" }
        return ByteArray(32) { hex.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
    }

    /** Bindet jeden Satz an Datei und Stelle: vertauschte Sätze öffnen sich nicht. */
    private fun aad(name: String, index: Int) = "msm-tresor-teilen-v1:$name:$index".toByteArray(Charsets.UTF_8)

    /** „Tresor“ steht im Teilen-Menü nur, solange die Kamera-Sicherung eingerichtet ist. */
    fun zielSetzen(ctx: Context, an: Boolean) {
        ctx.packageManager.setComponentEnabledSetting(
            ComponentName(ctx, TeilenActivity::class.java),
            if (an) PackageManager.COMPONENT_ENABLED_STATE_ENABLED else PackageManager.COMPONENT_ENABLED_STATE_DISABLED,
            PackageManager.DONT_KILL_APP,
        )
    }

    fun vorhanden(ctx: Context, auftrag: JSONObject): Boolean = datei(ctx, auftrag).exists()

    fun entfernen(ctx: Context, auftrag: JSONObject) {
        datei(ctx, auftrag).delete()
    }

    /** Klartext von Satz `index`. */
    fun lesen(ctx: Context, auftrag: JSONObject, index: Int): ByteArray {
        val d = datei(ctx, auftrag)
        val schluessel = schluessel(auftrag)
        try {
            RandomAccessFile(d, "r").use { f ->
                val von = index.toLong() * SATZ
                val laenge = minOf(SATZ, f.length() - von)
                if (laenge <= IV + GCM) throw IOException("Satz fehlt")
                val satz = ByteArray(laenge.toInt())
                f.seek(von)
                f.readFully(satz)
                val c = Cipher.getInstance("AES/GCM/NoPadding")
                c.init(Cipher.DECRYPT_MODE, SecretKeySpec(schluessel, "AES"), GCMParameterSpec(GCM * 8, satz, 0, IV))
                c.updateAAD(aad(d.name, index))
                return c.doFinal(satz, IV, satz.size - IV)
            }
        } finally {
            schluessel.fill(0)
        }
    }

    /** Kopien ohne Auftrag (Teilen abgebrochen, Auftrag unlesbar). */
    fun aufraeumen(ctx: Context, kennung: String) {
        val genannt = KameraAblage.auftraege(ctx, kennung).mapNotNull { it.optJSONObject("lokal")?.optString("datei") }.toSet()
        val grenze = System.currentTimeMillis() - VERWAIST_MS
        ordner(ctx).listFiles().orEmpty().forEach { f ->
            if (f.name !in genannt && f.lastModified() < grenze) f.delete()
        }
    }

    /**
     * Liest die geteilte Datei, legt sie verschlüsselt ab und schreibt den
     * Auftrag. `false`, wenn die Sicherung inzwischen aus oder neu eingerichtet
     * ist; dann bleibt nichts liegen.
     */
    fun aufnehmen(ctx: Context, stand: KameraAblage.Stand, uri: Uri, typHinweis: String?, fortschritt: (Long) -> Unit): Boolean {
        var name = ""
        var groesse = -1L
        var geaendert: Long? = null
        try {
            ctx.contentResolver.query(uri, null, null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 && !c.isNull(it) }?.let { name = c.getString(it) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 && !c.isNull(it) }?.let { groesse = c.getLong(it) }
                    c.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED).takeIf { it >= 0 && !c.isNull(it) }?.let {
                        geaendert = c.getLong(it).takeIf { ms -> ms > 0 }
                    }
                }
            }
        } catch (e: Exception) {
            // Manche Apps geben keine Angaben heraus; die Datei selbst zählt.
        }
        val ziel = ordner(ctx)
        if (groesse >= 0 && ziel.usableSpace < groesse + RESERVE) throw KeinPlatz()

        val typ = (ctx.contentResolver.getType(uri) ?: typHinweis ?: "").take(255)
        name = name.replace(UNERWUENSCHT, "_").trim().take(255).ifEmpty { "Geteilt_${System.currentTimeMillis()}" }

        val datei = File(ziel, "${UUID.randomUUID()}.bin")
        val schluessel = ByteArray(32).also { zufall.nextBytes(it) }
        try {
            val (sha, echt) = ctx.contentResolver.openInputStream(uri)?.use { ein ->
                verschluesseln(ein, datei, schluessel, ziel, fortschritt)
            } ?: throw IOException("Datei nicht lesbar")

            val bilder = when {
                typ.startsWith("image/") -> KameraBilder.angaben(ctx, uri, false)
                typ.startsWith("video/") -> KameraBilder.angaben(ctx, uri, true)
                else -> KameraBilder.Angaben()
            }
            val angaben = JSONObject().apply {
                put("name", name)
                put("typ", typ)
                geaendert?.let { put("geaendert", it) }
                // Damit der Tresor ein Foto, das schon aus einem gesicherten Ordner kam, nicht doppelt anlegt.
                put("sha256", sha)
            }
            val lokal = JSONObject().put("datei", datei.name).put("schluessel", Medien.hex(schluessel))
            val auftrag = KameraAuftrag.bauen(stand, angaben, bilder, echt, sha, JSONObject().put("lokal", lokal))
            if (!KameraAblage.auftragSchreiben(ctx, stand.kennung, auftrag)) {
                datei.delete()
                return false
            }
            return true
        } catch (e: Exception) {
            datei.delete()
            throw e
        } finally {
            schluessel.fill(0)
        }
    }

    /** Schreibt Satz für Satz; gibt SHA-256 und Größe des Klartexts zurück. */
    private fun verschluesseln(ein: InputStream, datei: File, schluessel: ByteArray, ziel: File, fortschritt: (Long) -> Unit): Pair<String, Long> {
        val sha = MessageDigest.getInstance("SHA-256")
        val puffer = ByteArray(KameraKrypto.CHUNK.toInt())
        var echt = 0L
        var index = 0
        try {
            datei.outputStream().use { aus ->
                while (true) {
                    var n = 0
                    while (n < puffer.size) {
                        val r = ein.read(puffer, n, puffer.size - n)
                        if (r < 0) break
                        n += r
                    }
                    if (n == 0) break
                    if (ziel.usableSpace < SATZ + RESERVE) throw KeinPlatz()
                    sha.update(puffer, 0, n)
                    val iv = ByteArray(IV).also { zufall.nextBytes(it) }
                    val c = Cipher.getInstance("AES/GCM/NoPadding")
                    c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(schluessel, "AES"), GCMParameterSpec(GCM * 8, iv))
                    c.updateAAD(aad(datei.name, index))
                    aus.write(iv)
                    aus.write(c.doFinal(puffer, 0, n))
                    echt += n
                    index++
                    fortschritt(echt)
                    if (n < puffer.size) break
                }
            }
        } finally {
            puffer.fill(0)
        }
        return Medien.hex(sha.digest()) to echt
    }
}
