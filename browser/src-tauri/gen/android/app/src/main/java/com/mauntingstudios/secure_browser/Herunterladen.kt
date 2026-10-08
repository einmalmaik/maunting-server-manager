package com.mauntingstudios.secure_browser

import android.app.Activity
import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Base64
import android.webkit.URLUtil
import androidx.core.content.ContextCompat
import androidx.webkit.ProfileStore
import org.json.JSONObject
import java.io.OutputStream
import java.util.concurrent.Executors

/**
 * Downloads unter Android. Adressen über http(s) lädt der Download-Dienst des
 * Systems nach `Download/`, mit Benachrichtigung, Cookies aus dem Profil des
 * Tabs und dessen User-Agent. `blob:` und `data:` kennt nur die Seite: `seite.js`
 * holt sie und gibt sie in Teilen weiter, jeden erst nach der Quittung für den
 * vorigen; sie gehen per MediaStore nach `Download/` (AGENTS.md Punkt 69).
 *
 * Gemeldet wird wie unter Windows als `download` mit eigener `nr`; den Namen
 * bereinigt Rust (`TabsBruecke.dateiname`). Einen Virenschutz gibt es nicht.
 */
class Herunterladen(private val activity: Activity) {
  private val dienst = activity.getSystemService(DownloadManager::class.java)
  private var naechste = 1L

  /** Laufende Downloads des Dienstes: seine Kennung → Tab, `nr`, Adresse. */
  private val imDienst = HashMap<Long, Triple<String, Long, String>>()

  /** Laufende Dateien aus der Seite, nach `nr`. Nur auf dem UI-Faden. */
  private val ausSeite = HashMap<Long, SeitenDatei>()

  /** Schreibt der Reihe nach; die Seite schickt erst nach der Quittung weiter. */
  private val schreiber = Executors.newSingleThreadExecutor()
  private val ui = Handler(Looper.getMainLooper())

  private val fertig = object : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
      val kennung = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1)
      // Nur eigene Kennungen; der Stand kommt vom Dienst, nicht aus dem Intent.
      val (tab, nr, url) = imDienst.remove(kennung) ?: return
      var datei: String? = null
      dienst.query(DownloadManager.Query().setFilterById(kennung))?.use { c ->
        if (c.moveToFirst() && c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) == DownloadManager.STATUS_SUCCESSFUL) {
          datei = c.getString(c.getColumnIndexOrThrow(DownloadManager.COLUMN_LOCAL_URI))?.let { Uri.parse(it).lastPathSegment }
        }
      }
      melden(tab, nr, if (datei != null) "fertig" else "fehler", url, datei)
    }
  }

  init {
    // Exportiert: der Download-Dienst ist eine eigene App, kein Systemprozess.
    ContextCompat.registerReceiver(
      activity, fertig, IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE), ContextCompat.RECEIVER_EXPORTED,
    )
  }

  private fun melden(tab: String, nr: Long, stand: String, url: String, datei: String? = null) {
    val json = JSONObject().put("art", "download").put("id", tab).put("nr", nr).put("stand", stand)
      .put("url", url).put("datei", datei ?: JSONObject.NULL)
    TabsBruecke.melden(json.toString())
  }

  /** `DownloadListener` eines Tabs. */
  fun start(tab: Tab, url: String, userAgent: String, disposition: String?, mime: String?) {
    val nr = naechste++
    val anzeige = url.take(Tab.MAX_ADRESSE)
    val name = TabsBruecke.dateiname(url, URLUtil.guessFileName(url, disposition, mime))
    melden(tab.id, nr, "start", anzeige)
    if (name.isEmpty()) return melden(tab.id, nr, "fehler", anzeige)
    when {
      url.startsWith("https://") || url.startsWith("http://") -> try {
        val auftrag = DownloadManager.Request(Uri.parse(url))
          .setMimeType(mime)
          .addRequestHeader("User-Agent", userAgent)
          .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
          .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
        ProfileStore.getInstance().getProfile(tab.profil)?.cookieManager?.getCookie(url)
          ?.let { auftrag.addRequestHeader("Cookie", it) }
        imDienst[dienst.enqueue(auftrag)] = Triple(tab.id, nr, anzeige)
      } catch (e: Exception) {
        melden(tab.id, nr, "fehler", anzeige)
      }
      url.startsWith("blob:") || url.startsWith("data:") -> {
        ausSeite[nr] = SeitenDatei(tab.id, nr, anzeige, name, mime ?: "application/octet-stream")
        val bitte = JSONObject().put("t", "datei").put("nr", nr).put("url", url).toString()
        if (!tab.senden(bitte)) abbrechen(nr)
      }
      else -> melden(tab.id, nr, "fehler", anzeige)
    }
  }

  /**
   * Ein Teil von `seite.js`: `daten` (Base64), am Ende `ende`, bei einem
   * Fehler `fehler`. Nur für eine Datei, um die dieser Tab gebeten wurde.
   */
  fun teil(tab: Tab, n: JSONObject) {
    val nr = n.optLong("nr", -1)
    val datei = ausSeite[nr]?.takeIf { it.tab == tab.id } ?: return
    val daten = n.optString("daten")
    // Ein Teil vor der Quittung des vorigen oder zu groß: die Seite hält sich
    // nicht an den Ablauf, und ihre Teile lägen sonst ungebremst im Speicher.
    if (n.optBoolean("fehler") || datei.inArbeit || daten.length > TEIL_HOECHSTENS) return abbrechen(nr, tab)
    datei.inArbeit = true
    val name = n.optString("name").takeIf { it.isNotBlank() }?.let { TabsBruecke.dateiname("", it) }
    val ende = n.optBoolean("ende")
    schreiber.execute {
      val weiter = runCatching { datei.schreiben(name, daten, ende) }.getOrDefault(false)
      ui.post {
        datei.inArbeit = false
        if (ausSeite[nr] !== datei) return@post
        when {
          !weiter -> abbrechen(nr, tab)
          ende -> {
            ausSeite.remove(nr)
            melden(datei.tab, nr, "fertig", datei.url, datei.angelegt)
          }
          else -> tab.senden(quittung(nr, true))
        }
      }
    }
  }

  private fun quittung(nr: Long, weiter: Boolean) = JSONObject().put("t", "weiter").put("nr", nr).put("ok", weiter).toString()

  /** Bricht eine Datei aus der Seite ab; was schon geschrieben war, fällt weg. */
  private fun abbrechen(nr: Long, tab: Tab? = null) {
    val datei = ausSeite.remove(nr) ?: return
    tab?.senden(quittung(nr, false))
    schreiber.execute { datei.verwerfen() }
    melden(datei.tab, nr, "fehler", datei.url)
  }

  /** Der Tab hat die Seite gewechselt oder ist zu: deren Dateien kommen nicht mehr. */
  fun seiteWeg(tab: String) {
    ausSeite.values.filter { it.tab == tab }.map { it.nr }.forEach { abbrechen(it) }
  }

  fun zeigen() {
    activity.startActivity(Intent(DownloadManager.ACTION_VIEW_DOWNLOADS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
  }

  /** Eine Datei aus der Seite in `Download/`; bis zum Ende `IS_PENDING`. Nur auf [schreiber]. */
  private inner class SeitenDatei(val tab: String, val nr: Long, val url: String, val name: String, val mime: String) {
    private var uri: Uri? = null
    private var aus: OutputStream? = null
    private var groesse = 0L
    /** Ein Teil wird gerade geschrieben; nur auf dem UI-Faden. */
    var inArbeit = false
    var angelegt: String? = null

    fun schreiben(besserer: String?, daten: String, ende: Boolean): Boolean {
      val ablage = activity.contentResolver
      val ziel = uri ?: ablage.insert(
        MediaStore.Downloads.EXTERNAL_CONTENT_URI,
        ContentValues().apply {
          put(MediaStore.Downloads.DISPLAY_NAME, besserer?.takeIf { it.isNotEmpty() } ?: name)
          put(MediaStore.Downloads.MIME_TYPE, mime)
          put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
          put(MediaStore.Downloads.IS_PENDING, 1)
        },
      )?.also { uri = it } ?: return false
      val strom = aus ?: ablage.openOutputStream(ziel)?.also { aus = it } ?: return false
      if (daten.isNotEmpty()) {
        val bytes = Base64.decode(daten, Base64.DEFAULT)
        groesse += bytes.size
        if (groesse > HOECHSTENS) return false
        strom.write(bytes)
      }
      if (!ende) return true
      strom.close()
      aus = null
      ablage.update(ziel, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
      ablage.query(ziel, arrayOf(MediaStore.Downloads.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) angelegt = c.getString(0)
      }
      return true
    }

    fun verwerfen() {
      runCatching { aus?.close() }
      uri?.let { runCatching { activity.contentResolver.delete(it, null, null) } }
    }
  }

  companion object {
    /** Was eine Seite höchstens als eine Datei hinterlegen kann (AGENTS.md Punkt 27). */
    const val HOECHSTENS = 1L shl 30

    /** Ein Teil von `seite.js` (256 KiB) in Base64, mit Luft. */
    const val TEIL_HOECHSTENS = 512 * 1024
  }
}
