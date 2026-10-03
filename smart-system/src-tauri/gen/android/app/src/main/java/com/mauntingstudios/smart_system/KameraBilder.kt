package com.mauntingstudios.smart_system

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.media.MediaMetadataRetriever
import android.os.Build
import java.io.ByteArrayOutputStream
import java.util.Calendar
import java.util.TimeZone

/**
 * Miniatur, Vorschau und Aufnahmedaten einer Aufnahme, wie `bildAngaben` in
 * `tresorBilder.ts`: WebP mit höchstens 256 bzw. 1600 px an der langen Kante,
 * so klein, dass es in 32 KiB bzw. 512 KiB passt. Scheitert nie; im Zweifel
 * fehlt etwas, und die Datei zeigt im Tresor ein Symbol statt eines Bildes.
 */
object KameraBilder {
    private const val MINIATUR_KANTE = 256
    private const val VORSCHAU_KANTE = 1600

    class Angaben {
        var breite: Int? = null
        var hoehe: Int? = null
        var dauer: Double? = null
        var aufgenommen: Long? = null
        var kamera: String? = null
        var vorschau: ByteArray = ByteArray(0)
        var miniatur: ByteArray = ByteArray(0)
    }

    fun angaben(ctx: Context, aufnahme: Aufnahme): Angaben {
        val a = Angaben()
        try {
            val bild = if (aufnahme.art == "video") video(ctx, aufnahme, a) else foto(ctx, aufnahme, a)
            if (bild != null) {
                try {
                    a.vorschau = webp(bild, VORSCHAU_KANTE, KameraKrypto.VORSCHAU.toInt())
                    a.miniatur = webp(bild, MINIATUR_KANTE, KameraKrypto.MINIATUR.toInt())
                } finally {
                    bild.recycle()
                }
            }
        } catch (e: Exception) {
            // Nicht darstellbar: keine Bilder.
        } catch (e: OutOfMemoryError) {
            // Ebenso; das Original geht trotzdem in den Tresor.
        }
        return a
    }

    private fun foto(ctx: Context, aufnahme: Aufnahme, a: Angaben): Bitmap? {
        val uri = Medien.original(aufnahme.id, aufnahme.art)
        val drehung = ctx.contentResolver.openInputStream(uri)?.use { ein ->
            val exif = ExifInterface(ein)
            val hersteller = exif.getAttribute(ExifInterface.TAG_MAKE)?.trim().orEmpty()
            val modell = exif.getAttribute(ExifInterface.TAG_MODEL)?.trim().orEmpty()
            val kamera = if (modell.lowercase().startsWith(hersteller.lowercase())) modell else "$hersteller $modell".trim()
            if (kamera.isNotEmpty()) a.kamera = kamera.take(80)
            a.aufgenommen = exifZeit(exif.getAttribute(ExifInterface.TAG_DATETIME_ORIGINAL) ?: exif.getAttribute(ExifInterface.TAG_DATETIME))
            when (exif.getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                ExifInterface.ORIENTATION_ROTATE_90, ExifInterface.ORIENTATION_TRANSPOSE -> 90
                ExifInterface.ORIENTATION_ROTATE_180 -> 180
                ExifInterface.ORIENTATION_ROTATE_270, ExifInterface.ORIENTATION_TRANSVERSE -> 270
                else -> 0
            }
        } ?: 0

        val masse = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        ctx.contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, masse) }
        if (masse.outWidth <= 0 || masse.outHeight <= 0) return null
        val gedreht = drehung == 90 || drehung == 270
        a.breite = if (gedreht) masse.outHeight else masse.outWidth
        a.hoehe = if (gedreht) masse.outWidth else masse.outHeight

        var teiler = 1
        while (maxOf(masse.outWidth, masse.outHeight) / (teiler * 2) >= VORSCHAU_KANTE) teiler *= 2
        val roh = ctx.contentResolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = teiler })
        } ?: return null
        if (drehung == 0) return roh
        val gedrehtesBild = Bitmap.createBitmap(roh, 0, 0, roh.width, roh.height, Matrix().apply { postRotate(drehung.toFloat()) }, true)
        if (gedrehtesBild !== roh) roh.recycle()
        return gedrehtesBild
    }

    private fun video(ctx: Context, aufnahme: Aufnahme, a: Angaben): Bitmap? {
        val leser = MediaMetadataRetriever()
        try {
            leser.setDataSource(ctx, Medien.adresse(aufnahme.id, aufnahme.art))
            val ms = leser.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLongOrNull()
            if (ms != null) a.dauer = ms / 1000.0
            val b = leser.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_WIDTH)?.toIntOrNull()
            val h = leser.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_HEIGHT)?.toIntOrNull()
            val drehung = leser.extractMetadata(MediaMetadataRetriever.METADATA_KEY_VIDEO_ROTATION)?.toIntOrNull() ?: 0
            if (b != null && h != null) {
                a.breite = if (drehung % 180 != 0) h else b
                a.hoehe = if (drehung % 180 != 0) b else h
            }
            // Wie im WebView: ein Bild bei einem Zehntel der Dauer, höchstens nach einer Sekunde.
            val zeit = minOf(1_000_000L, (ms ?: 0L) * 100)
            return if (Build.VERSION.SDK_INT >= 27) {
                leser.getScaledFrameAtTime(zeit, MediaMetadataRetriever.OPTION_CLOSEST_SYNC, VORSCHAU_KANTE, VORSCHAU_KANTE)
            } else {
                leser.getFrameAtTime(zeit, MediaMetadataRetriever.OPTION_CLOSEST_SYNC)
            }
        } finally {
            leser.release()
        }
    }

    /** `yyyy:MM:dd HH:mm:ss` als Wanduhrzeit in UTC, wie `exifLesen` im WebView. */
    internal fun exifZeit(text: String?): Long? {
        val t = Regex("^(\\d{4}):(\\d{2}):(\\d{2}) (\\d{2}):(\\d{2}):(\\d{2})").find(text ?: return null) ?: return null
        val (j, mo, tag, h, mi, s) = t.destructured
        if (j.toInt() <= 1970) return null
        return Calendar.getInstance(TimeZone.getTimeZone("UTC")).run {
            clear()
            set(j.toInt(), mo.toInt() - 1, tag.toInt(), h.toInt(), mi.toInt(), s.toInt())
            timeInMillis
        }
    }

    private fun webp(bild: Bitmap, kante: Int, grenze: Int): ByteArray {
        val faktor = minOf(1.0, kante.toDouble() / maxOf(bild.width, bild.height))
        val b = maxOf(1, Math.round(bild.width * faktor).toInt())
        val h = maxOf(1, Math.round(bild.height * faktor).toInt())
        val klein = if (b == bild.width && h == bild.height) bild else Bitmap.createScaledBitmap(bild, b, h, true)
        try {
            @Suppress("DEPRECATION")
            val format = if (Build.VERSION.SDK_INT >= 30) Bitmap.CompressFormat.WEBP_LOSSY else Bitmap.CompressFormat.WEBP
            for (qualitaet in intArrayOf(82, 70, 55, 40, 25)) {
                val aus = ByteArrayOutputStream()
                klein.compress(format, qualitaet, aus)
                if (aus.size() <= grenze) return aus.toByteArray()
            }
            return ByteArray(0)
        } finally {
            if (klein !== bild) klein.recycle()
        }
    }
}
