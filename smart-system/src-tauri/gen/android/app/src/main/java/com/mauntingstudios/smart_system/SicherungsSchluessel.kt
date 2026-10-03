package com.mauntingstudios.smart_system

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Der Unterschriftsschlüssel eines Geräts für den Posteingang der Kamera-Sicherung.
 *
 * ECDSA P-256 im Android Keystore, nicht exportierbar. Mit ihm unterschreibt das
 * Gerät jeden Datensatz, den es bei gesperrtem Tresor ablegt. Den öffentlichen
 * Teil trägt die App bei offenem Tresor als verschlüsselten Eintrag ein; die
 * Übernahme verwirft alles, was kein eingetragenes Gerät unterschrieben hat
 * (AGENTS.md Punkt 41). Den öffentlichen Posteingang-Schlüssel kann jeder
 * kennen, der dieses Telefon ausliest, die Unterschrift nicht.
 *
 * Ein Schlüssel je Installation und Tresor (`geraet` ist eine zufällige UUID).
 */
object SicherungsSchluessel {
    private const val KEYSTORE = "AndroidKeyStore"
    private val UUID = Regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    private fun alias(geraet: String): String {
        require(UUID.matches(geraet)) { "Ungültige Gerätekennung" }
        return "msm-kamera-$geraet"
    }

    private fun speicher(): KeyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }

    /** Legt den Schlüssel bei Bedarf an und gibt den öffentlichen Teil als SPKI (DER) zurück. */
    fun oeffentlich(geraet: String): ByteArray {
        val alias = alias(geraet)
        val ks = speicher()
        if (!ks.containsAlias(alias)) {
            val erzeuger = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE)
            erzeuger.initialize(
                KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build(),
            )
            erzeuger.generateKeyPair()
        }
        return ks.getCertificate(alias).publicKey.encoded
    }

    /** Unterschreibt `daten` (SHA-256) und gibt r ‖ s mit je 32 Bytes zurück, wie WebCrypto es prüft. */
    fun unterschreiben(geraet: String, daten: ByteArray): ByteArray {
        val alias = alias(geraet)
        val eintrag = speicher().getEntry(alias, null) as? KeyStore.PrivateKeyEntry
            ?: throw IllegalStateException("Kein Schlüssel für dieses Gerät")
        val der = Signature.getInstance("SHA256withECDSA").run {
            initSign(eintrag.privateKey)
            update(daten)
            sign()
        }
        return derZuRoh(der)
    }

    /** DER `SEQUENCE { INTEGER r, INTEGER s }` → r ‖ s, je 32 Bytes. */
    internal fun derZuRoh(der: ByteArray): ByteArray {
        var pos = 0
        fun laenge(): Int {
            val erstes = der[pos++].toInt() and 0xff
            if (erstes < 0x80) return erstes
            var wert = 0
            repeat(erstes and 0x7f) { wert = (wert shl 8) or (der[pos++].toInt() and 0xff) }
            return wert
        }
        fun ganzzahl(): ByteArray {
            require(der[pos++] == 0x02.toByte()) { "Keine Ganzzahl" }
            val n = laenge()
            val wert = BigInteger(1, der.copyOfRange(pos, pos + n))
            pos += n
            val bytes = wert.toByteArray().dropWhile { it == 0.toByte() }.toByteArray()
            require(bytes.size <= 32) { "Zahl zu groß" }
            return ByteArray(32 - bytes.size) + bytes
        }
        require(der[pos++] == 0x30.toByte()) { "Keine Folge" }
        laenge()
        return ganzzahl() + ganzzahl()
    }
}
