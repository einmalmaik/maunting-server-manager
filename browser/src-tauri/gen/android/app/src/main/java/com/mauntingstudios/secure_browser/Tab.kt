package com.mauntingstudios.secure_browser

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.net.ConnectivityManager
import android.net.http.SslError
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.webkit.HttpAuthHandler
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebSettingsCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject
import java.io.ByteArrayInputStream

/**
 * Ein Tab: eine `android.webkit.WebView` im Profil [profil] (`seiten` oder
 * das private, `TabsPlugin`). Die WebView kennt weder die
 * Tauri-Brücke noch ein `JavascriptInterface`; mit Rust spricht nur dieser
 * Code über [TabsBruecke], mit der Seite nur `seite.js` über `msbKanal`.
 */
@SuppressLint("SetJavaScriptEnabled", "ClickableViewAccessibility")
class Tab(private val plugin: TabsPlugin, context: Context, val id: String, val privat: Boolean, val profil: String) {
  val webView = WebView(context)

  /** Adresse der Seite für den Schild; gelesen auf dem Faden der Anfragen. */
  @Volatile private var seite = ""

  /**
   * Hauptrahmen, den die Sperre beim Laden abgewiesen hat (ein POST kommt an
   * `weg` vorbei). Rust hat `gesperrt` schon gemeldet; ein `laedt` danach
   * hätte die Sperrseite der Oberfläche wieder weggenommen.
   */
  @Volatile private var abgewiesen: String? = null

  /**
   * Der Tab zeigt eine abgewiesene Seite: die leere Antwort der Sperre. Titel
   * („Webseite nicht verfügbar“), Symbol und `geladen` gehen dann nicht an die
   * Oberfläche, sonst stünde sie mit diesem Titel im Verlauf.
   */
  private var stumm = false

  /**
   * Die Bildsuche lädt unter der Zieladresse eine Seite, die das Foto
   * abschickt; so ist der POST für die Suchmaschine kein fremder, und sie
   * bekommt ihre `SameSite=Lax`-Cookies (Googles Zustimmung). Sobald eine
   * andere Seite fertig ist, fällt sie aus dem Verlauf: Zurück schickte das
   * Foto sonst noch einmal. Hier steht die Zieladresse, solange das aussteht.
   */
  private var bildsucheVergessen: String? = null

  /**
   * Die laufende Bildsuche. Leitet die Suchmaschine erst auf eine eigene Seite
   * um (Googles Zustimmung zu Cookies) und danach per GET zurück an die
   * Zieladresse, kam das Foto nie an; dann geht es einmal neu ab. Mit diesem
   * Versuch, nach [BILD_FRIST] und mit jedem `laden` fällt es aus dem Speicher.
   */
  private var bild: Bildsuche? = null

  private class Bildsuche(val ziel: String, val seite: String, val seit: Long)

  /** Rückweg zu `seite.js` im obersten Rahmen der aktuellen Seite. */
  private var kanal: JavaScriptReplyProxy? = null
  /** Herkunft des Dokuments, dem [kanal] gehört (`sourceOrigin`, nicht `webView.url`). */
  private var kanalHerkunft = ""
  private var tippX = 0f
  private var tippY = 0f

  init {
    // Vor allem anderen: danach lässt sich das Profil nicht mehr wählen.
    WebViewCompat.setProfile(webView, profil)
    einstellen(webView.settings)
    WebViewCompat.addDocumentStartJavaScript(webView, TabsBruecke.seitenskript(), setOf("*"))
    WebViewCompat.addWebMessageListener(webView, "msbKanal", setOf("*")) { view, nachricht, quelle, hauptrahmen, antwort ->
      val text = nachricht.data
      // `view.url` ist nach `loadUrl` schon die neue Adresse, während noch das
      // alte Dokument spricht. Zählt nur, wenn beide dieselbe Herkunft haben.
      val herkunft = quelle.toString()
      if (hauptrahmen && text != null && TabsBruecke.gleicheHerkunft(view.url.orEmpty(), herkunft)) {
        kanal = antwort
        kanalHerkunft = herkunft
        // Teile einer Datei (`Herunterladen.kt`) und das Hallo, das den Rückweg
        // öffnet, bleiben hier; alles andere entscheidet Rust.
        val art = if (text.startsWith("{\"t\":")) runCatching { JSONObject(text) }.getOrNull() else null
        when (art?.optString("t")) {
          "da" -> {}
          "teil" -> plugin.herunterladen.teil(this, art)
          else -> TabsBruecke.nachricht(id, view.url.orEmpty(), text)
        }
      }
    }
    webView.webViewClient = Klient()
    webView.webChromeClient = TabChrome(plugin, this)
    webView.setFindListener { aktiv, anzahl, fertig ->
      if (fertig) melden("treffer", "aktuell" to (if (anzahl > 0) aktiv + 1 else 0), "anzahl" to anzahl)
    }
    webView.setOnTouchListener { _, e ->
      if (e.actionMasked == MotionEvent.ACTION_DOWN) {
        tippX = e.x
        tippY = e.y
      }
      false
    }
    webView.setOnLongClickListener { langDruecken() }
    webView.setDownloadListener { url, userAgent, disposition, mime, _ ->
      plugin.herunterladen.start(this, url, userAgent, disposition, mime)
    }
  }

  private fun einstellen(s: WebSettings) {
    s.javaScriptEnabled = true
    s.domStorageEnabled = true
    s.allowFileAccess = false
    s.allowContentAccess = false
    s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
    s.setGeolocationEnabled(false)
    s.setSupportMultipleWindows(true)
    s.javaScriptCanOpenWindowsAutomatically = false
    s.builtInZoomControls = true
    s.displayZoomControls = false
    s.useWideViewPort = true
    s.loadWithOverviewMode = true
    // Wie SmartScreen unter Windows: keine besuchten Adressen an Google.
    if (WebViewFeature.isFeatureSupported(WebViewFeature.SAFE_BROWSING_ENABLE)) {
      WebSettingsCompat.setSafeBrowsingEnabled(s, false)
    }
    // Ohne „; wv“ und „Version/4.0“ halten Seiten den Tab für Chrome statt für
    // eine eingebettete WebView, die etwa Google bei der Anmeldung ablehnt.
    s.userAgentString = s.userAgentString.replace("; wv)", ")").replace(Regex("Version/[0-9.]+ "), "")
  }

  /** Lädt [url] nach Prüfung durch Rust; `loadUrl` geht an `shouldOverrideUrlLoading` vorbei. */
  fun laden(url: String) {
    bild = null
    when (val ziel = TabsBruecke.weg(id, url, false)) {
      "-" -> return
      "" -> webView.loadUrl(url)
      else -> webView.loadUrl(ziel)
    }
  }

  fun bildsuche(ziel: String, seite: String) {
    bild = Bildsuche(ziel, seite, SystemClock.elapsedRealtime())
    abschicken(ziel, seite)
  }

  private fun abschicken(ziel: String, seite: String) {
    bildsucheVergessen = ziel
    webView.loadDataWithBaseURL(ziel, seite, "text/html", "utf-8", "about:blank")
  }

  /** Kommt die Bildsuche ohne Foto an ihr Ziel zurück? Dann schickt sie es noch einmal. */
  private fun bildZurueck(anfrage: WebResourceRequest): Boolean {
    val b = bild ?: return false
    if (anfrage.method != "GET" || ohneAbfrage(anfrage.url.toString()) != ohneAbfrage(b.ziel)) return false
    bild = null
    if (SystemClock.elapsedRealtime() - b.seit > BILD_FRIST) return false
    webView.post { abschicken(b.ziel, b.seite) }
    return true
  }

  /** An `seite.js` der aktuellen Seite; `false`, wenn sie noch keinen Rückweg geöffnet hat. */
  fun senden(nachricht: String): Boolean {
    val k = kanal ?: return false
    k.postMessage(nachricht)
    return true
  }

  /** Füllt, wenn der Tab noch auf der Herkunft von [fuer] steht. */
  fun fuellen(fuer: String, nachricht: String): Boolean {
    val k = kanal ?: return false
    if (!TabsBruecke.gleicheHerkunft(kanalHerkunft, fuer) || !TabsBruecke.gleicheHerkunft(webView.url.orEmpty(), fuer)) return false
    k.postMessage(nachricht)
    return true
  }

  fun melden(art: String, vararg felder: Pair<String, Any?>) {
    if (stumm && art in STUMM) return
    val json = JSONObject().put("art", art).put("id", id)
    for ((name, wert) in felder) json.put(name, wert ?: JSONObject.NULL)
    TabsBruecke.melden(json.toString())
  }

  /** Nur Link und Bild: auf Text bleibt die Markierung von Android. */
  private fun langDruecken(): Boolean {
    val treffer = webView.hitTestResult
    val ziel = treffer.extra?.takeIf { it.length <= MAX_ADRESSE }
    fun zeigen(link: String?, bild: String?) {
      val nr = Rueckfragen.neu(id) { }
      melden(
        "kontextmenue", "nr" to nr, "x" to tippX.toInt(), "y" to tippY.toInt(), "eintraege" to org.json.JSONArray(),
        "link" to link, "bild" to bild, "auswahl" to null, "bearbeitbar" to false,
      )
    }
    when (treffer.type) {
      WebView.HitTestResult.SRC_ANCHOR_TYPE -> zeigen(ziel ?: return false, null)
      WebView.HitTestResult.IMAGE_TYPE -> zeigen(null, ziel ?: return false)
      WebView.HitTestResult.SRC_IMAGE_ANCHOR_TYPE -> {
        // Das Bild nennt `extra`, den Link nur die WebView auf Nachfrage.
        val antwort = Handler(Looper.getMainLooper()) { m ->
          zeigen(m.data.getString("url")?.takeIf { it.length <= MAX_ADRESSE }, ziel)
          true
        }.obtainMessage()
        webView.requestFocusNodeHref(antwort)
      }
      else -> return false
    }
    return true
  }

  private inner class Klient : WebViewClient() {
    override fun shouldOverrideUrlLoading(view: WebView, anfrage: WebResourceRequest): Boolean {
      // Rahmen prüft `anfrage` beim Laden; Rust entscheidet über die Seite.
      if (!anfrage.isForMainFrame) return false
      if (bildZurueck(anfrage)) return true
      return when (val ziel = TabsBruecke.weg(id, anfrage.url.toString(), anfrage.isRedirect)) {
        "" -> false
        "-" -> true
        else -> {
          view.loadUrl(ziel)
          true
        }
      }
    }

    override fun shouldInterceptRequest(view: WebView, anfrage: WebResourceRequest): WebResourceResponse? {
      val url = anfrage.url.toString()
      if (anfrage.isForMainFrame) seite = url
      val accept = anfrage.requestHeaders["Accept"].orEmpty()
      if (!TabsBruecke.anfrage(id, url, seite, anfrage.isForMainFrame, accept)) return null
      if (anfrage.isForMainFrame) abgewiesen = url
      return WebResourceResponse("text/plain", "utf-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
    }

    override fun onPageStarted(view: WebView, url: String, favicon: Bitmap?) {
      seite = url
      kanal = null
      kanalHerkunft = ""
      plugin.herunterladen.seiteWeg(id)
      TabsBruecke.seitenwechsel(id)
      stumm = abgewiesen == url
      abgewiesen = null
      if (stumm) return
      melden("laedt", "url" to url)
    }

    override fun onPageFinished(view: WebView, url: String) {
      if (bildsucheVergessen.let { it != null && it != url } && url.startsWith("https://")) {
        bildsucheVergessen = null
        view.clearHistory()
        plugin.zurueckPruefen()
      }
      melden("geladen", "url" to url)
      kosmetik(view, url)
    }

    override fun doUpdateVisitedHistory(view: WebView, url: String, neuGeladen: Boolean) {
      seite = url
      melden("adresse", "url" to url, "zurueck" to view.canGoBack(), "vor" to view.canGoForward())
      plugin.zurueckPruefen()
    }

    override fun onReceivedError(view: WebView, anfrage: WebResourceRequest, fehler: WebResourceError) {
      if (!anfrage.isForMainFrame) return
      // Abgebrochen hat der Browser selbst (geblockt, Download, neue Navigation).
      if (fehler.description?.contains("ERR_ABORTED") == true) return
      if (zurueckfallen(view, anfrage.url.toString())) return
      melden("fehlerseite", "url" to anfrage.url.toString(), "grund" to grund(view.context, fehler.errorCode))
    }

    override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, fehler: SslError) {
      handler.cancel()
      if (fehler.url != seite || zurueckfallen(view, fehler.url)) return
      melden("fehlerseite", "url" to fehler.url, "grund" to "zertifikat")
    }

    /** Eben erst auf HTTPS hochgestuft, und die Seite kann es nicht: wie angegeben laden. */
    private fun zurueckfallen(view: WebView, url: String): Boolean {
      val ziel = TabsBruecke.rueckfall(url).takeIf { it.isNotEmpty() } ?: return false
      view.loadUrl(ziel)
      return true
    }

    override fun onReceivedHttpAuthRequest(view: WebView, handler: HttpAuthHandler, host: String, bereich: String) {
      val nr = Rueckfragen.neu(id) { a ->
        if (a == null || a.isNull("benutzer")) handler.cancel()
        else handler.proceed(a.getString("benutzer"), a.optString("passwort"))
      }
      melden("anmeldung", "nr" to nr, "herkunft" to host, "bereich" to bereich)
    }

    override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
      plugin.abgestuerzt(this@Tab)
      melden("absturz")
      return true
    }
  }

  private fun kosmetik(view: WebView, url: String) {
    TabsBruecke.kosmetik(url).takeIf { it.isNotEmpty() }?.let { view.evaluateJavascript(it, null) }
    val sammeln = TabsBruecke.klassenSammeln(url).takeIf { it.isNotEmpty() } ?: return
    view.evaluateJavascript(sammeln) { json ->
      // Inzwischen auf einer anderen Seite: deren Regeln kommen mit deren Ende.
      if (view.url != url || json == null) return@evaluateJavascript
      TabsBruecke.allgemein(url, json).takeIf { it.isNotEmpty() }?.let { view.evaluateJavascript(it, null) }
    }
  }

  fun sichtbar(vorne: Boolean) {
    webView.visibility = if (vorne) View.VISIBLE else View.INVISIBLE
  }

  companion object {
    /** Längere Adressen (meist `data:`-Bilder) gehen nicht an die Oberfläche. */
    const val MAX_ADRESSE = 8192

    /** Was eine abgewiesene Seite nicht meldet (`stumm`). */
    private val STUMM = setOf("geladen", "titel", "favicon")

    /** So lange darf ein Umweg der Bildsuche dauern (eine Zustimmungsseite liest man). */
    private const val BILD_FRIST = 10 * 60 * 1000L

    private fun ohneAbfrage(url: String) = url.substringBefore('#').substringBefore('?')

    /** Gründe wie `fehler_art` in `tabs/desktop/ohne_edge.rs`: adresse, offline, zeit, verbindung, zertifikat, unbekannt. */
    fun grund(context: Context, code: Int): String {
      val netz = context.getSystemService(ConnectivityManager::class.java)?.activeNetwork
      if (netz == null) return "offline"
      return when (code) {
        WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_BAD_URL, WebViewClient.ERROR_UNSUPPORTED_SCHEME -> "adresse"
        WebViewClient.ERROR_TIMEOUT -> "zeit"
        WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_IO, WebViewClient.ERROR_PROXY_AUTHENTICATION -> "verbindung"
        WebViewClient.ERROR_FAILED_SSL_HANDSHAKE -> "zertifikat"
        else -> "unbekannt"
      }
    }
  }
}
