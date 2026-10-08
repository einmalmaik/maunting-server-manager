package com.mauntingstudios.secure_browser

import android.app.Activity
import android.app.DownloadManager
import android.content.ContentValues
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Base64
import android.webkit.CookieManager
import android.webkit.URLUtil
import androidx.webkit.ProfileStore
import org.json.JSONObject
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Downloads unter Android, alle per MediaStore nach `Download/` (AGENTS.md
 * Punkt 69). Adressen über http(s) lädt die App selbst: sie folgt
 * Weiterleitungen selbst, prüft jede an der Sperre und schickt Cookies aus dem
 * Profil des Tabs nur an den Host, dem sie gehören. Der Download-Dienst des
 * Systems tat das bis 09.10.2026: er schickte die Cookies der ersten Adresse
 * bei jeder Weiterleitung mit, auch an fremde Server, und folgte
 * Weiterleitungen an der Sperre vorbei. `blob:` und `data:` kennt nur die
 * Seite: `seite.js` holt sie und gibt sie in Teilen weiter, jeden erst nach
 * der Quittung für den vorigen.
 *
 * Gemeldet wird wie unter Windows als `download` mit eigener `nr`; den Namen
 * bereinigt Rust (`TabsBruecke.dateiname`). Einen Virenschutz gibt es nicht.
 *
 * Eine Seite kann Downloads per Skript in Schleife auslösen. Je Tab laufen
 * deshalb höchstens [JE_TAB] zugleich, und geschrieben wird nur, solange auf
 * dem Gerät [RESERVE] frei bleibt (AGENTS.md Punkt 27 und 78).
 */
class Herunterladen(private val activity: Activity) {
  private var naechste = 1L

  /** Laufende Dateien aus der Seite, nach `nr`. Nur auf dem UI-Faden. */
  private val ausSeite = HashMap<Long, SeitenDatei>()

  /** Laufende Downloads je Tab. Nur auf dem UI-Faden. */
  private val jeTab = HashMap<String, Int>()

  /** Schreibt der Reihe nach; die Seite schickt erst nach der Quittung weiter. */
  private val schreiber = Executors.newSingleThreadExecutor()

  /** Downloads aus dem Netz, höchstens drei zugleich. */
  private val netz = Executors.newFixedThreadPool(3)
  private val ui = Handler(Looper.getMainLooper())

  private fun melden(tab: String, nr: Long, stand: String, url: String, datei: String? = null) {
    val json = JSONObject().put("art", "download").put("id", tab).put("nr", nr).put("stand", stand)
      .put("url", url).put("datei", datei ?: JSONObject.NULL)
    TabsBruecke.melden(json.toString())
  }

  /** Meldet das Ende eines Downloads und gibt seinen Platz im Tab frei. */
  private fun beenden(tab: String, nr: Long, stand: String, url: String, datei: String? = null) {
    val rest = (jeTab[tab] ?: 1) - 1
    if (rest > 0) jeTab[tab] = rest else jeTab.remove(tab)
    melden(tab, nr, stand, url, datei)
  }

  /** `DownloadListener` eines Tabs. */
  fun start(tab: Tab, url: String, userAgent: String, disposition: String?, mime: String?) {
    val nr = naechste++
    val anzeige = url.take(Tab.MAX_ADRESSE)
    val name = TabsBruecke.dateiname(url, URLUtil.guessFileName(url, disposition, mime))
    melden(tab.id, nr, "start", anzeige)
    if (name.isEmpty() || (jeTab[tab.id] ?: 0) >= JE_TAB) return melden(tab.id, nr, "fehler", anzeige)
    if (url.startsWith("https://") || url.startsWith("http://") || url.startsWith("blob:") || url.startsWith("data:")) {
      jeTab[tab.id] = (jeTab[tab.id] ?: 0) + 1
    }
    when {
      url.startsWith("https://") || url.startsWith("http://") -> {
        // Das Profil gibt es nur auf dem UI-Faden; seine Cookies lassen sich von überall lesen.
        val cookies = ProfileStore.getInstance().getProfile(tab.profil)?.cookieManager
        netz.execute {
          val ergebnis = runCatching { ausDemNetz(url, userAgent, cookies, name, mime) }
          val datei = ergebnis.getOrNull()
          val stand = if (datei != null) "fertig" else if (ergebnis.exceptionOrNull() is KeinPlatz) "speicher" else "fehler"
          ui.post { beenden(tab.id, nr, stand, anzeige, datei) }
        }
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
   * Lädt [start] nach `Download/` und gibt den angelegten Namen zurück, `null`
   * bei Sperre, Fehler oder zu vielen Weiterleitungen. Auf [netz].
   */
  private fun ausDemNetz(start: String, userAgent: String, cookies: CookieManager?, name: String, mime: String?): String? {
    var url = start
    repeat(WEITERLEITUNGEN + 1) {
      if (TabsBruecke.dateiname(url, name).isEmpty()) return null
      val verbindung = URL(url).openConnection() as HttpURLConnection
      try {
        verbindung.instanceFollowRedirects = false
        verbindung.connectTimeout = 15_000
        verbindung.readTimeout = 60_000
        verbindung.setRequestProperty("User-Agent", userAgent)
        cookies?.getCookie(url)?.let { verbindung.setRequestProperty("Cookie", it) }
        val code = verbindung.responseCode
        if (code in 300..399) {
          url = URL(URL(url), verbindung.getHeaderField("Location") ?: return null).toString()
          if (!url.startsWith("https://") && !url.startsWith("http://")) return null
          return@repeat
        }
        if (code !in 200..299) return null
        val art = verbindung.contentType?.substringBefore(';')?.trim()?.takeIf { it.isNotEmpty() }
        val ablage = Ablage(name, mime ?: art ?: "application/octet-stream", Long.MAX_VALUE)
        try {
          verbindung.inputStream.use { ein ->
            val puffer = ByteArray(64 * 1024)
            while (true) {
              val n = ein.read(puffer)
              if (n < 0) break
              if (!ablage.schreiben(puffer, n)) return null.also { ablage.verwerfen() }
            }
          }
          return ablage.fertig()
        } catch (e: Exception) {
          ablage.verwerfen()
          throw e
        }
      } finally {
        verbindung.disconnect()
      }
    }
    return null
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
      val ergebnis = runCatching { datei.schreiben(name, daten, ende) }
      val weiter = ergebnis.getOrDefault(false)
      ui.post {
        datei.inArbeit = false
        if (ausSeite[nr] !== datei) return@post
        when {
          !weiter -> abbrechen(nr, tab, if (ergebnis.exceptionOrNull() is KeinPlatz) "speicher" else "fehler")
          ende -> {
            ausSeite.remove(nr)
            beenden(datei.tab, nr, "fertig", datei.url, datei.angelegt)
          }
          else -> tab.senden(quittung(nr, true))
        }
      }
    }
  }

  private fun quittung(nr: Long, weiter: Boolean) = JSONObject().put("t", "weiter").put("nr", nr).put("ok", weiter).toString()

  /** Bricht eine Datei aus der Seite ab; was schon geschrieben war, fällt weg. */
  private fun abbrechen(nr: Long, tab: Tab? = null, stand: String = "fehler") {
    val datei = ausSeite.remove(nr) ?: return
    tab?.senden(quittung(nr, false))
    schreiber.execute { datei.verwerfen() }
    beenden(datei.tab, nr, stand, datei.url)
  }

  /** Der Tab hat die Seite gewechselt oder ist zu: deren Dateien kommen nicht mehr. */
  fun seiteWeg(tab: String) {
    ausSeite.values.filter { it.tab == tab }.map { it.nr }.forEach { abbrechen(it) }
  }

  fun zeigen() {
    activity.startActivity(Intent(DownloadManager.ACTION_VIEW_DOWNLOADS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
  }

  /** Eine Datei in `Download/`, bis zum Ende `IS_PENDING`. Immer nur von einem Faden zugleich. */
  private inner class Ablage(private val name: String, private val mime: String, private val hoechstens: Long) {
    private var uri: Uri? = null
    private var aus: OutputStream? = null
    private var groesse = 0L
    private var geprueftBis = 0L
    private var reserviert: String? = null

    /** Legt beim ersten Aufruf an; [besserer] ersetzt dann den Namen. */
    fun schreiben(bytes: ByteArray, n: Int, besserer: String? = null): Boolean {
      val ablage = activity.contentResolver
      val ziel = uri ?: ablage.insert(
        MediaStore.Downloads.EXTERNAL_CONTENT_URI,
        ContentValues().apply {
          put(MediaStore.Downloads.DISPLAY_NAME, reservieren(besserer?.takeIf { it.isNotEmpty() } ?: name).also { reserviert = it })
          put(MediaStore.Downloads.MIME_TYPE, mime)
          put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
          put(MediaStore.Downloads.IS_PENDING, 1)
        },
      )?.also { uri = it } ?: return false
      val strom = aus ?: ablage.openOutputStream(ziel)?.also { aus = it } ?: return false
      groesse += n
      if (groesse > hoechstens) return false
      if (groesse >= geprueftBis) {
        if (Environment.getExternalStorageDirectory().usableSpace - n < RESERVE) throw KeinPlatz()
        geprueftBis = groesse + PRUEFEN_ALLE
      }
      strom.write(bytes, 0, n)
      return true
    }

    /** Schließt ab und gibt den Namen zurück, den Android vergeben hat. */
    fun fertig(): String? {
      if (uri == null && !schreiben(ByteArray(0), 0)) return null
      val ziel = uri ?: return null
      val ablage = activity.contentResolver
      aus?.close()
      aus = null
      ablage.update(ziel, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
      freigeben(reserviert)
      return ablage.query(ziel, arrayOf(MediaStore.Downloads.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
      } ?: name
    }

    fun verwerfen() {
      runCatching { aus?.close() }
      uri?.let { runCatching { activity.contentResolver.delete(it, null, null) } }
      freigeben(reserviert)
    }
  }

  /** Eine Datei aus der Seite. Nur auf [schreiber]. */
  private inner class SeitenDatei(val tab: String, val nr: Long, val url: String, name: String, mime: String) {
    private val ablage = Ablage(name, mime, HOECHSTENS)
    /** Ein Teil wird gerade geschrieben; nur auf dem UI-Faden. */
    var inArbeit = false
    var angelegt: String? = null

    fun schreiben(besserer: String?, daten: String, ende: Boolean): Boolean {
      val bytes = if (daten.isNotEmpty()) Base64.decode(daten, Base64.DEFAULT) else ByteArray(0)
      if (!ablage.schreiben(bytes, bytes.size, besserer)) return false
      if (ende) angelegt = ablage.fertig() ?: return false
      return true
    }

    fun verwerfen() = ablage.verwerfen()
  }

  /**
   * Namen, die gerade als `IS_PENDING` angelegt sind. Android legt zwei
   * gleichnamige, die in derselben Sekunde beginnen, in dieselbe Zwischendatei
   * (`.pending-<zeit>-<name>`): beide schrieben hinein, und eine Datei fehlte
   * danach (bis 09.10.2026, im Emulator). Ein zweiter bekommt deshalb vorher
   * „name (2).ext“.
   */
  private val belegt = HashSet<String>()

  private fun reservieren(wunsch: String): String = synchronized(belegt) {
    var name = wunsch
    var nr = 2
    while (!belegt.add(name)) name = mitNummer(wunsch, nr++)
    name
  }

  private fun freigeben(name: String?) {
    if (name != null) synchronized(belegt) { belegt.remove(name) }
  }

  /** Auf dem Gerät bliebe weniger als [RESERVE] frei. */
  class KeinPlatz : Exception()

  companion object {
    /** `bild.png` → `bild (2).png`; die Endung bleibt. */
    fun mitNummer(name: String, nr: Int): String {
      val punkt = name.lastIndexOf('.')
      return if (punkt > 0) "${name.substring(0, punkt)} ($nr)${name.substring(punkt)}" else "$name ($nr)"
    }

    /** Downloads, die ein Tab zugleich laufen lassen kann. */
    const val JE_TAB = 3

    /** Was auf dem Gerät frei bleibt; darunter bricht ein Download ab. */
    const val RESERVE = 512L shl 20

    /** Der freie Platz wird alle so viele geschriebene Bytes neu gelesen. */
    const val PRUEFEN_ALLE = 8L shl 20

    /** Was eine Seite höchstens als eine Datei hinterlegen kann (AGENTS.md Punkt 27). */
    const val HOECHSTENS = 1L shl 30

    /** Ein Teil von `seite.js` (256 KiB) in Base64, mit Luft. */
    const val TEIL_HOECHSTENS = 512 * 1024

    /** So viele Weiterleitungen folgt ein Download, wie Chromium. */
    const val WEITERLEITUNGEN = 20
  }
}
