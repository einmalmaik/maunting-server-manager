package com.mauntingstudios.smart_system

/**
 * Die Verschlüsselung der Kamera-Sicherung, in Rust (`kamera_krypto.rs`), Byte
 * für Byte wie DIS im WebView. Der Hintergrund-Job hat keinen WebView.
 *
 * Nur Verschlüsseln: den privaten Schlüssel des Posteingangs kennt das Telefon
 * nicht. Fehler kommen als `IllegalArgumentException`.
 */
object KameraKrypto {
    init {
        System.loadLibrary("maunting_smart_system_lib")
    }

    /** DIS `hybridEncrypt` als Bytes: ML-KEM-768 + RSA-4096-OAEP, AES-256-GCM mit `aad`. */
    @JvmStatic
    external fun hybrid(klartext: ByteArray, pq: ByteArray, rsaSpki: ByteArray, aad: ByteArray): ByteArray

    /** `chunkVerschluesseln` aus `tresorDatei.ts`: IV ‖ Chiffrat ‖ Tag, auf die Größenklasse gepolstert. */
    @JvmStatic
    external fun chunk(
        schluessel: ByteArray,
        daten: ByteArray,
        groesse: Long,
        echt: Long,
        index: Int,
        eintragId: String,
        blobId: String,
    ): ByteArray

    /** `gepolsterteGroesse` aus `tresorDatei.ts`. */
    @JvmStatic
    external fun gepolstert(echt: Long): Long

    const val CHUNK = 4L * 1024 * 1024
    const val UEBERHANG = 28L
    const val MINIATUR = 32L * 1024
    const val VORSCHAU = 512L * 1024

    fun chunkAnzahl(groesse: Long): Int = maxOf(1L, (groesse + CHUNK - 1) / CHUNK).toInt()

    fun chunkLaenge(groesse: Long, index: Int): Long = minOf(CHUNK, groesse - index * CHUNK)

    /** Echte Bytes in Chunk `index`; der Rest ist Polsterung. */
    fun echteBytes(groesse: Long, echt: Long, index: Int): Int =
        maxOf(0L, minOf(chunkLaenge(groesse, index), echt - index * CHUNK)).toInt()

    fun chiffratGroesse(groesse: Long): Long = groesse + chunkAnzahl(groesse) * UEBERHANG
}
