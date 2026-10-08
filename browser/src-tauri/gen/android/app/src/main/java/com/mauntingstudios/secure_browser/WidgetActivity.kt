package com.mauntingstudios.secure_browser

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.os.Bundle
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File

/**
 * Was das Such-Widget (`SuchWidget.kt`) anstößt: Suche, Spracheingabe oder
 * ein Foto für die Bildsuche. Durchsichtig und nicht exportiert; nur das
 * Widget erreicht sie über seine `PendingIntent`s.
 *
 * Das Ergebnis legt sie in [wartend], im eigenen Prozess, und öffnet danach
 * den Browser. `MainActivity` liest keine Extras: eine fremde App kann so
 * keinen Suchbegriff und keine Adresse hineinreichen.
 *
 * Aufgenommen und erkannt wird von den System-Apps, der Browser braucht weder
 * Kamera- noch Mikrofonrecht. Das Foto liegt nur in `cache/fotos/` und fällt,
 * sobald die Suche es abgeschickt hat (`TabsPlugin.bildsuche`).
 */
class WidgetActivity : AppCompatActivity() {
  private val sprache = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
    val text = r.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()?.trim()
    if (!text.isNullOrEmpty()) oeffnen(JSONObject().put("art", "text").put("text", text.take(MAX_TEXT)))
    else finish()
  }

  private val kamera = registerForActivityResult(ActivityResultContracts.TakePicture()) { aufgenommen ->
    val roh = rohFoto(this)
    if (aufgenommen && runCatching { verkleinern(roh, foto(this)) }.isSuccess) {
      oeffnen(JSONObject().put("art", "bild"))
    } else {
      finish()
    }
    roh.delete()
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    // Nach einem Neuaufbau (Kamera im Vordergrund, Prozess beendet) liefert
    // der Launcher das Ergebnis nach; nicht ein zweites Mal starten.
    if (savedInstanceState != null) return
    when (intent.action) {
      SPRACHE -> try {
        sprache.launch(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(
          RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_WEB_SEARCH,
        ))
      } catch (e: ActivityNotFoundException) {
        Toast.makeText(this, R.string.widget_keine_sprache, Toast.LENGTH_LONG).show()
        oeffnen(JSONObject().put("art", "suche"))
      }
      KAMERA -> try {
        val ziel = rohFoto(this).also { it.parentFile?.mkdirs() }
        kamera.launch(FileProvider.getUriForFile(this, "$packageName.fileprovider", ziel))
      } catch (e: ActivityNotFoundException) {
        Toast.makeText(this, R.string.widget_keine_kamera, Toast.LENGTH_LONG).show()
        finish()
      }
      else -> oeffnen(JSONObject().put("art", "suche"))
    }
  }

  private fun oeffnen(start: JSONObject) {
    wartend = start
    startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    finish()
  }

  companion object {
    const val SUCHE = "com.mauntingstudios.secure_browser.widget.SUCHE"
    const val SPRACHE = "com.mauntingstudios.secure_browser.widget.SPRACHE"
    const val KAMERA = "com.mauntingstudios.secure_browser.widget.KAMERA"

    /** Mehr spricht niemand in eine Suche; der Rest fällt weg. */
    const val MAX_TEXT = 2000

    /** Längste Kante des Fotos, das an die Suchmaschine geht. */
    private const val KANTE = 1600

    /** Was der Browser beim nächsten Abholen öffnet (`TabsPlugin.startAbholen`). */
    @Volatile var wartend: JSONObject? = null

    fun rohFoto(context: Context) = File(context.cacheDir, "fotos/aufnahme.jpg")

    fun foto(context: Context) = File(context.cacheDir, "fotos/suche.jpg")

    /**
     * Höchstens [KANTE] Pixel, aufrecht gedreht (die Kamera schreibt die Lage
     * nur in die EXIF-Daten), als JPEG ohne Metadaten: kein Ort, kein Gerät.
     */
    fun verkleinern(roh: File, ziel: File) {
      val masse = BitmapFactory.Options().apply { inJustDecodeBounds = true }
      BitmapFactory.decodeFile(roh.path, masse)
      var teiler = 1
      while (maxOf(masse.outWidth, masse.outHeight) / (teiler * 2) >= KANTE) teiler *= 2
      val bild = BitmapFactory.decodeFile(roh.path, BitmapFactory.Options().apply { inSampleSize = teiler })
        ?: throw IllegalStateException("Kein Bild")
      val grad = when (ExifInterface(roh.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
        ExifInterface.ORIENTATION_ROTATE_90 -> 90f
        ExifInterface.ORIENTATION_ROTATE_180 -> 180f
        ExifInterface.ORIENTATION_ROTATE_270 -> 270f
        else -> 0f
      }
      val faktor = minOf(1f, KANTE.toFloat() / maxOf(bild.width, bild.height))
      val matrix = Matrix().apply { postScale(faktor, faktor); postRotate(grad) }
      val fertig = Bitmap.createBitmap(bild, 0, 0, bild.width, bild.height, matrix, true)
      ziel.outputStream().use { fertig.compress(Bitmap.CompressFormat.JPEG, 85, it) }
      if (fertig !== bild) fertig.recycle()
      bild.recycle()
    }
  }
}
