package com.mauntingstudios.secure_browser

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Canvas
import android.net.Uri
import android.print.PrintManager
import android.util.Base64
import android.view.View
import android.view.ViewGroup
import android.webkit.ServiceWorkerClient
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.webkit.ProfileStore
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.UUID

@InvokeArg class TabArgs { lateinit var id: String }
@InvokeArg class LadenArgs { lateinit var id: String; lateinit var url: String; var privat = false }
@InvokeArg class VorneArgs { var vorne: String? = null }
@InvokeArg class RahmenArgs { var x = 0.0; var y = 0.0; var breite = 0.0; var hoehe = 0.0 }
@InvokeArg class AktionArgs { lateinit var id: String; lateinit var aktion: String }
@InvokeArg class AntwortArgs { var nr = 0L; lateinit var antwort: String }
@InvokeArg class SuchenArgs { lateinit var id: String; lateinit var richtung: String; var begriff = "" }
@InvokeArg class FuellenArgs { lateinit var id: String; lateinit var fuer: String; var rahmen: String? = null; lateinit var nachricht: String }
@InvokeArg class StandbildArgs { lateinit var id: String; var png = false }
@InvokeArg class BildSpeichernArgs { lateinit var id: String; lateinit var name: String; var url = ""; lateinit var png: String }
@InvokeArg class BildsucheArgs { lateinit var id: String; var privat = false; lateinit var url: String; lateinit var feld: String; var base64 = false }

/**
 * Die Tabs unter Android (`src/tabs/android.rs`): je Tab eine WebView über
 * der Oberfläche, im Inhaltsbereich, den die Oberfläche in CSS-Pixeln nennt.
 *
 * Rust ruft von einem eigenen Faden; jeder Befehl läuft auf dem UI-Faden und
 * antwortet erst dort. Seiten liegen im Profil [SEITEN], private Tabs
 * gemeinsam in einem eigenen, wie ein Inkognito-Fenster. Die Oberfläche
 * bleibt im Standardprofil.
 *
 * Ein Profil, das in diesem Prozess eine WebView hatte, lässt Android nicht
 * löschen („Cannot delete in-use profile“, auch nach `destroy`). Schließt der
 * letzte private Tab, leert der Browser deshalb Cookies, Speicher und Cache
 * des privaten Profils sofort; den Ordner löscht der nächste Start.
 */
@TauriPlugin
class TabsPlugin(private val activity: Activity) : Plugin(activity) {
  private val tabs = HashMap<String, Tab>()
  private var vorne: String? = null
  private var rahmen = RahmenArgs()
  private var vollbild: Pair<View, WebChromeClient.CustomViewCallback>? = null
  private val privatProfil = PRIVAT + UUID.randomUUID()
  private val inhalt: ViewGroup get() = activity.findViewById(android.R.id.content)
  val herunterladen by lazy { Herunterladen(activity) }

  /** Zurück gehört dem Tab, solange er vorne liegt und zurück kann; sonst der Oberfläche. */
  private val zurueck = object : OnBackPressedCallback(false) {
    override fun handleOnBackPressed() {
      if (vollbild != null) return vollbildBeenden()
      vorne?.let { tabs[it] }?.webView?.goBack()
    }
  }

  override fun load(webView: WebView) {
    activity.runOnUiThread {
      // Nach dem Rückweg von Tauri angemeldet und damit vor ihm gefragt.
      (activity as AppCompatActivity).onBackPressedDispatcher.addCallback(activity, zurueck)
      // Private Profile früherer Läufe; jetzt hat sie keine WebView.
      val store = ProfileStore.getInstance()
      store.allProfileNames.filter { it.startsWith(PRIVAT) }.forEach { runCatching { store.deleteProfile(it) } }
    }
  }

  private fun tab(id: String): Tab = tabs[id] ?: throw IllegalStateException("Kein Tab $id")

  fun zurueckPruefen() {
    val tab = vorne?.let { tabs[it] }
    zurueck.isEnabled = vollbild != null || tab?.webView?.canGoBack() == true
  }

  @Command
  fun laden(invoke: Invoke) {
    val a = invoke.parseArgs(LadenArgs::class.java)
    aufUi(activity, invoke) {
      tabHolen(a.id, a.privat).laden(a.url)
      null
    }
  }

  private fun tabHolen(id: String, privat: Boolean): Tab = tabs.getOrPut(id) {
    val profil = if (privat) privatProfil else SEITEN
    ProfileStore.getInstance().getOrCreateProfile(profil)
    Tab(this, activity, id, privat, profil).also {
      if (mitWorkerPruefung.add(profil)) workerPruefen(profil)
      it.sichtbar(false)
      inhalt.addView(it.webView, platz())
    }
  }

  /** Profile, deren Service Worker schon geprüft werden. */
  private val mitWorkerPruefung = HashSet<String>()

  /**
   * Anfragen eines Service Workers sieht `shouldInterceptRequest` des Tabs
   * nicht; Schild und Jugendschutz prüfen sie deshalb je Profil hier. Bis
   * 09.10.2026 lud eine Seite mit Service Worker darüber, was die Sperre
   * abweisen sollte. Die Seite steht nicht fest: der Referer, sonst die Adresse.
   */
  private fun workerPruefen(name: String) {
    val profil = ProfileStore.getInstance().getProfile(name) ?: return
    profil.serviceWorkerController.setServiceWorkerClient(object : ServiceWorkerClient() {
      override fun shouldInterceptRequest(anfrage: WebResourceRequest): WebResourceResponse? {
        val url = anfrage.url.toString()
        val seite = anfrage.requestHeaders["Referer"] ?: url
        if (!TabsBruecke.anfrage("", url, seite, false, anfrage.requestHeaders["Accept"].orEmpty())) return null
        return WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
      }
    })
  }

  @Command
  fun sichtbarkeit(invoke: Invoke) {
    val a = invoke.parseArgs(VorneArgs::class.java)
    aufUi(activity, invoke) {
      vorne = a.vorne
      for ((id, tab) in tabs) tab.sichtbar(id == vorne)
      zurueckPruefen()
      null
    }
  }

  @Command
  fun rahmen(invoke: Invoke) {
    val a = invoke.parseArgs(RahmenArgs::class.java)
    aufUi(activity, invoke) {
      rahmen = a
      for (tab in tabs.values) tab.webView.layoutParams = platz()
      null
    }
  }

  /** Der Inhaltsbereich in Gerätepixeln. */
  private fun platz(): FrameLayout.LayoutParams {
    val d = activity.resources.displayMetrics.density
    return FrameLayout.LayoutParams((rahmen.breite * d).toInt(), (rahmen.hoehe * d).toInt()).apply {
      leftMargin = (rahmen.x * d).toInt()
      topMargin = (rahmen.y * d).toInt()
    }
  }

  @Command
  fun schliessen(invoke: Invoke) {
    val a = invoke.parseArgs(TabArgs::class.java)
    aufUi(activity, invoke) {
      tabs.remove(a.id)?.let { wegraeumen(it) }
      if (vorne == a.id) vorne = null
      zurueckPruefen()
      null
    }
  }

  /** Das Schild hat sich geändert: jeder Tab bekommt das Cookie-Skript neu. */
  @Command
  fun cookies(invoke: Invoke) {
    aufUi(activity, invoke) {
      tabs.values.forEach { it.cookiesErneuern() }
      null
    }
  }

  private fun wegraeumen(tab: Tab) {
    Rueckfragen.tabWeg(tab.id)
    herunterladen.seiteWeg(tab.id)
    inhalt.removeView(tab.webView)
    if (tab.privat && tabs.values.none { it.privat }) profilLeeren(tab.profil, tab.webView)
    tab.webView.destroy()
  }

  /** Cookies, Speicher und Cache eines Profils; [ansicht] liegt darin (für den Cache). */
  private fun profilLeeren(name: String, ansicht: WebView) {
    val profil = ProfileStore.getInstance().getOrCreateProfile(name)
    profil.cookieManager.removeAllCookies(null)
    profil.cookieManager.flush()
    profil.webStorage.deleteAllData()
    ansicht.clearCache(true)
  }

  /** Der Renderer ist weg: die WebView ist tot; `laden` legt eine neue an. */
  fun abgestuerzt(tab: Tab) {
    tabs.remove(tab.id)?.let { wegraeumen(it) }
    zurueckPruefen()
  }

  /** Zurück, Vor, Neu laden, Anhalten über die WebView, nie als Skript der Seite. */
  @Command
  fun aktion(invoke: Invoke) {
    val a = invoke.parseArgs(AktionArgs::class.java)
    aufUi(activity, invoke) {
      val ansicht = tab(a.id).webView
      when (a.aktion) {
        "zurueck" -> ansicht.goBack()
        "vor" -> ansicht.goForward()
        "neu_laden" -> ansicht.reload()
        else -> ansicht.stopLoading()
      }
      null
    }
  }

  @Command
  fun standbild(invoke: Invoke) {
    val a = invoke.parseArgs(StandbildArgs::class.java)
    aufUi(activity, invoke) {
      val ansicht = tab(a.id).webView
      // Das Standbild beim Verdecken als JPEG, der Screenshot verlustfrei als PNG.
      val bild = Bitmap.createBitmap(ansicht.width.coerceAtLeast(1), ansicht.height.coerceAtLeast(1), if (a.png) Bitmap.Config.ARGB_8888 else Bitmap.Config.RGB_565)
      ansicht.draw(Canvas(bild))
      val bytes = ByteArrayOutputStream().also {
        if (a.png) bild.compress(Bitmap.CompressFormat.PNG, 100, it) else bild.compress(Bitmap.CompressFormat.JPEG, 80, it)
      }.toByteArray()
      bild.recycle()
      JSObject().put("bild", Base64.encodeToString(bytes, Base64.NO_WRAP))
    }
  }

  /** Ein Screenshot nach `Download/`; den Namen hat Rust bereinigt (`tabs/aufnahme.rs`). */
  @Command
  fun bildSpeichern(invoke: Invoke) {
    val a = invoke.parseArgs(BildSpeichernArgs::class.java)
    aufUi(activity, invoke) {
      val datei = herunterladen.bild(a.id, a.name, a.url, Base64.decode(a.png, Base64.DEFAULT))
        ?: throw IllegalStateException("Das Bild ließ sich nicht speichern")
      JSObject().put("datei", datei)
    }
  }

  @Command
  fun antworten(invoke: Invoke) {
    val a = invoke.parseArgs(AntwortArgs::class.java)
    aufUi(activity, invoke) {
      Rueckfragen.antworten(a.nr, JSONObject(a.antwort))
      null
    }
  }

  @Command
  fun suchen(invoke: Invoke) {
    val a = invoke.parseArgs(SuchenArgs::class.java)
    aufUi(activity, invoke) {
      val ansicht = tab(a.id).webView
      when (a.richtung) {
        "start" -> ansicht.findAllAsync(a.begriff)
        "weiter" -> ansicht.findNext(true)
        "zurueck" -> ansicht.findNext(false)
        else -> ansicht.clearMatches()
      }
      null
    }
  }

  @Command
  fun fuellen(invoke: Invoke) {
    val a = invoke.parseArgs(FuellenArgs::class.java)
    aufUi(activity, invoke) {
      if (!tab(a.id).fuellen(a.fuer, a.rahmen, a.nachricht)) throw IllegalStateException("Die Seite hat inzwischen gewechselt")
      null
    }
  }

  @Command
  fun drucken(invoke: Invoke) {
    val a = invoke.parseArgs(TabArgs::class.java)
    aufUi(activity, invoke) {
      val ansicht = tab(a.id).webView
      val name = ansicht.title?.takeIf { it.isNotBlank() } ?: "Seite"
      activity.getSystemService(PrintManager::class.java).print(name, ansicht.createPrintDocumentAdapter(name), null)
      null
    }
  }

  /** Cookies, Speicher und Cache im Profil [SEITEN]; private Profile fallen mit ihrem Tab. */
  @Command
  fun datenLeeren(invoke: Invoke) {
    aufUi(activity, invoke) {
      val offen = tabs.values.firstOrNull { !it.privat }?.webView
      val ansicht = offen ?: WebView(activity).also { androidx.webkit.WebViewCompat.setProfile(it, SEITEN) }
      profilLeeren(SEITEN, ansicht)
      if (offen == null) ansicht.destroy()
      null
    }
  }

  @Command
  fun downloadsZeigen(invoke: Invoke) {
    aufUi(activity, invoke) {
      herunterladen.zeigen()
      null
    }
  }

  /**
   * Schickt das Foto des Widgets an die Bildsuche: eine Seite im Tab baut das
   * Formular und sendet es ab (`Tab.bildsuche`). Die Datei ist danach weg.
   */
  @Command
  fun bildsuche(invoke: Invoke) {
    val a = invoke.parseArgs(BildsucheArgs::class.java)
    aufUi(activity, invoke) {
      val datei = WidgetActivity.foto(activity)
      if (!datei.exists()) throw IllegalStateException("Kein Foto")
      val daten = try {
        Base64.encodeToString(datei.readBytes(), Base64.NO_WRAP)
      } finally {
        datei.delete()
      }
      tabHolen(a.id, a.privat).bildsuche(a.url, BildsucheSeite.bauen(a.url, a.feld, a.base64, daten))
      null
    }
  }

  /**
   * Eine Seite im Vollbild kann die Leisten des Browsers nachbauen. Wer gerade
   * den ganzen Bildschirm hat, steht deshalb jedes Mal in einem Hinweis, wie in
   * Chrome; Zurück beendet das Vollbild immer (`zurueck`).
   */
  fun vollbildZeigen(ansicht: View, rueckruf: WebChromeClient.CustomViewCallback, adresse: String?) {
    vollbild?.let { vollbildBeenden() }
    val host = adresse?.let { Uri.parse(it).host }.orEmpty()
    Toast.makeText(activity, activity.getString(R.string.vollbild_hinweis, host), Toast.LENGTH_LONG).show()
    vollbild = ansicht to rueckruf
    inhalt.addView(ansicht, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))
    val leisten = WindowCompat.getInsetsController(activity.window, inhalt)
    leisten.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    leisten.hide(WindowInsetsCompat.Type.systemBars())
    zurueckPruefen()
  }

  fun vollbildBeenden() {
    val (ansicht, rueckruf) = vollbild ?: return
    vollbild = null
    inhalt.removeView(ansicht)
    WindowCompat.getInsetsController(activity.window, inhalt).show(WindowInsetsCompat.Type.systemBars())
    rueckruf.onCustomViewHidden()
    zurueckPruefen()
  }

  fun dateiWaehlen(intent: Intent, fertig: (Int, Intent?) -> Unit) {
    (activity as MainActivity).launchActivityForResult(intent) { r ->
      fertig(r?.resultCode ?: Activity.RESULT_CANCELED, r?.data)
    }
  }

  companion object {
    const val SEITEN = "seiten"
    const val PRIVAT = "privat-"
  }
}
