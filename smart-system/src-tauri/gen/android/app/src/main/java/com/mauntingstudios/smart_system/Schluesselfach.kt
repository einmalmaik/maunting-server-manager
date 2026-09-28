package com.mauntingstudios.smart_system

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Der geschützte Schlüsselspeicher dieses Geräts.
 *
 * Das Gegenstück zum Windows Credential Store. Beide lösen dieselbe Aufgabe:
 * ein Geheimnis so verwahren, dass es eine kopierte Datenpartition nicht
 * hergibt und dass es erst nach einer Bestätigung durch den Menschen
 * herauskommt.
 *
 * Der Weg dorthin ist auf Android ein anderer. Es gibt keinen Dienst, der
 * Zeichenketten aufbewahrt, sondern den Hardware-Keystore: er hält einen
 * Schlüssel, den kein Prozess auslesen kann — auch dieser hier nicht —, und
 * gibt nur Ver- und Entschlüsselung damit her. Das Geheimnis selbst liegt
 * verschlüsselt in den app-eigenen Einstellungen. Wer die Datenpartition
 * kopiert, bekommt den Geheimtext und nicht den Schlüssel dazu, denn der
 * verlässt die Hardware nie.
 *
 * Zwei Sorten Fächer:
 *
 * - **mit Bestätigung** (`authNoetig = true`): der Keystore gibt den Schlüssel
 *   nur nach einem erfolgreichen BiometricPrompt frei. Hier liegt der
 *   Messenger-PIN. Neu angelernte Fingerabdrücke machen den Schlüssel
 *   ungültig — wer ein fremdes Gesicht hinzufügt, kommt damit nicht an das
 *   bereits Verwahrte.
 * - **ohne Bestätigung** (`authNoetig = false`): hardwaregebunden, aber ohne
 *   Abfrage. Hier liegt das Gerätegeheimnis des Messengers, das allein nichts
 *   öffnet und nur als zweite Hälfte in die Ableitung aus dem PIN eingeht.
 *   Dieselbe Aufteilung wie auf Windows, wo dieses Fach ebenfalls ohne Hello
 *   gelesen wird: eine Abfrage dort machte das reine PIN-Entsperren unmöglich.
 */
object Schluesselfach {

    private const val KEYSTORE = "AndroidKeyStore"
    private const val ABLAGE = "msm_schluesselfach"
    private const val GCM_TAG_BITS = 128

    /** Fächer, die von aussen kommen dürfen. Alles andere wird abgewiesen. */
    private val ERLAUBTE_FAECHER = setOf(
        "vault_biometric_key",
        "messenger_biometric_key",
        "messenger_device_secret",
    )

    /** Dieses Fach wird ohne Abfrage gelesen. Siehe Klassenkommentar. */
    private const val FACH_OHNE_ABFRAGE = "messenger_device_secret"

    fun brauchtBestaetigung(fach: String): Boolean = fach != FACH_OHNE_ABFRAGE

    fun pruefeFach(fach: String) {
        require(fach in ERLAUBTE_FAECHER) { "Unbekanntes Schlüsselfach: $fach" }
    }

    private fun alias(fach: String) = "msm_fach_$fach"

    private fun ablage(context: Context) =
        context.getSharedPreferences(ABLAGE, Context.MODE_PRIVATE)

    private fun keystore(): KeyStore =
        KeyStore.getInstance(KEYSTORE).apply { load(null) }

    fun hatEintrag(context: Context, fach: String): Boolean {
        pruefeFach(fach)
        return ablage(context).contains(fach)
    }

    /**
     * Legt den Schlüssel dieses Fachs an, falls es ihn noch nicht gibt.
     *
     * Bei `authNoetig` wird der Schlüssel an eine frische Bestätigung gebunden:
     * `0` Sekunden Gültigkeit heisst, dass jede einzelne Benutzung eine eigene
     * Freigabe braucht. Eine Gültigkeitsdauer wäre bequemer und würde die
     * Sperre aushöhlen — sie liefe weiter, während der Mensch längst weg ist.
     */
    private fun erzeugeSchluessel(fach: String, authNoetig: Boolean): SecretKey {
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
        val bauer = KeyGenParameterSpec.Builder(
            alias(fach),
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)

        if (authNoetig) {
            bauer.setUserAuthenticationRequired(true)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                bauer.setUserAuthenticationParameters(
                    0,
                    KeyProperties.AUTH_BIOMETRIC_STRONG or KeyProperties.AUTH_DEVICE_CREDENTIAL,
                )
            } else {
                @Suppress("DEPRECATION")
                bauer.setUserAuthenticationValidityDurationSeconds(-1)
            }
            // Ein neu angelernter Fingerabdruck macht den Schlüssel ungültig.
            // Ohne das käme jeder, der ein zweites Gesicht hinzufügen darf, an
            // alles, was vorher verwahrt wurde.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
                bauer.setInvalidatedByBiometricEnrollment(true)
            }
        }

        generator.init(bauer.build())
        return generator.generateKey()
    }

    private fun vorhandenerSchluessel(fach: String): SecretKey? =
        keystore().getKey(alias(fach), null) as? SecretKey

    private fun holeSchluessel(fach: String, authNoetig: Boolean): SecretKey =
        vorhandenerSchluessel(fach) ?: erzeugeSchluessel(fach, authNoetig)

    /**
     * Ein Cipher zum Verschlüsseln. Bei Fächern mit Bestätigung muss er durch
     * einen BiometricPrompt gereicht werden, bevor er benutzt werden darf.
     */
    fun cipherZumAblegen(fach: String): Cipher {
        pruefeFach(fach)
        val authNoetig = brauchtBestaetigung(fach)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, holeSchluessel(fach, authNoetig))
        return cipher
    }

    /**
     * Ein Cipher zum Lesen, vorbereitet mit dem abgelegten IV.
     *
     * Gibt `null` zurück, wenn zu diesem Fach nichts abgelegt ist — und auch
     * dann, wenn zwar ein Geheimtext daliegt, der Schlüssel dazu aber fehlt.
     * Hier wird bewusst keiner erzeugt: ein frischer Schlüssel machte aus einem
     * „da ist nichts" ein „der Inhalt ist gefälscht", denn der GCM-Tag scheiterte
     * erst beim Entschlüsseln. Beides endet ohne Geheimnis, aber nur das eine
     * sagt dem Menschen die Wahrheit.
     */
    fun cipherZumLesen(context: Context, fach: String): Cipher? {
        pruefeFach(fach)
        val roh = ablage(context).getString(fach, null) ?: return null
        val schluessel = vorhandenerSchluessel(fach) ?: return null
        val (iv, _) = zerlege(roh)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, schluessel, GCMParameterSpec(GCM_TAG_BITS, iv))
        return cipher
    }

    /**
     * Verschlüsselt und legt ab. Der Cipher kommt aus [cipherZumAblegen].
     *
     * `commit` statt `apply`: der Aufrufer meldet dem Menschen danach „Fingerabdruck
     * eingerichtet". Eine Schreibzusage, die noch in der Luft hängt, wäre an dieser
     * Stelle eine Lüge — stirbt der Prozess dazwischen, stünde der Schalter auf an
     * und das Fach wäre leer.
     */
    fun legeAb(context: Context, fach: String, cipher: Cipher, geheimnis: String) {
        pruefeFach(fach)
        val geheim = cipher.doFinal(geheimnis.toByteArray(Charsets.UTF_8))
        ablage(context).edit().putString(fach, baue(cipher.iv, geheim)).commit()
    }

    /** Liest und entschlüsselt. Der Cipher kommt aus [cipherZumLesen]. */
    fun lies(context: Context, fach: String, cipher: Cipher): String? {
        pruefeFach(fach)
        val roh = ablage(context).getString(fach, null) ?: return null
        val (_, geheim) = zerlege(roh)
        return String(cipher.doFinal(geheim), Charsets.UTF_8)
    }

    /**
     * Nimmt Eintrag und Schlüssel zurück.
     *
     * Beides gehört zusammen: ein zurückgelassener Keystore-Eintrag ohne
     * Geheimtext ist Müll, ein zurückgelassener Geheimtext ohne Schlüssel
     * erweckt den Eindruck, es läge noch etwas vor.
     */
    fun loesche(context: Context, fach: String) {
        pruefeFach(fach)
        // Auch hier `commit`: ein Löschen, das noch nicht auf der Platte steht,
        // ist kein Löschen.
        ablage(context).edit().remove(fach).commit()
        runCatching { keystore().deleteEntry(alias(fach)) }
    }

    // `iv:geheimtext`, beide base64. Der IV ist kein Geheimnis, er muss nur
    // beim Lesen wieder derselbe sein.
    private fun baue(iv: ByteArray, geheim: ByteArray): String =
        Base64.encodeToString(iv, Base64.NO_WRAP) + ":" +
            Base64.encodeToString(geheim, Base64.NO_WRAP)

    private fun zerlege(roh: String): Pair<ByteArray, ByteArray> {
        val teile = roh.split(":")
        require(teile.size == 2) { "Beschädigter Eintrag im Schlüsselfach" }
        return Base64.decode(teile[0], Base64.NO_WRAP) to Base64.decode(teile[1], Base64.NO_WRAP)
    }
}
