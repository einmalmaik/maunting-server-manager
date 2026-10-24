package com.mauntingstudios.secure_browser

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.util.Base64
import android.view.View
import android.webkit.GeolocationPermissions
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.PermissionRequest
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.content.ContextCompat
import androidx.webkit.WebViewCompat
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * Was die Seite vom Browser will: Titel, Symbol, neue Fenster, Dialoge,
 * Vollbild, Dateiauswahl, Kamera und Mikrofon. Standort lehnt der Tab ab:
 * die App deklariert dieses Recht nicht (Datenschutz).
 */
class TabChrome(private val plugin: TabsPlugin, private val tab: Tab) : WebChromeClient() {
  private class Wartet(val anfrage: PermissionRequest, val ressourcen: Array<String>) {
    var weg = false
  }

  companion object {
    /** Länger lebt ein Fenster nie, das keine Adresse nennt. */
    private const val HILFE_HOECHSTENS_MS = 10_000L

    /** Noch nicht beantwortete Systemabfragen, nach Tab. Alles auf dem UI-Faden. */
    private val wartend = HashMap<Long, Pair<String, Wartet>>()
    private var naechste = 0L

    /** Beim Schließen oder Verlassen der Seite gibt es niemanden mehr zu fragen. */
    fun verwerfen(tabId: String) {
      // Auch die Browser-Frage selbst (vor der Android-Abfrage) verfällt.
      Rueckfragen.tabWeg(tabId)
      val nummern = wartend.filterValues { it.first == tabId }.keys.toList()
      for (nr in nummern) {
        val wartet = wartend.remove(nr)?.second ?: continue
        wartet.weg = true
        runCatching { wartet.anfrage.deny() }
      }
    }
  }

  override fun onReceivedTitle(view: WebView, titel: String?) {
    tab.melden("titel", "titel" to titel.orEmpty())
  }

  override fun onReceivedIcon(view: WebView, symbol: Bitmap) {
    val klein = Bitmap.createScaledBitmap(symbol, 32, 32, true)
    val bytes = ByteArrayOutputStream().also { klein.compress(Bitmap.CompressFormat.PNG, 100, it) }.toByteArray()
    tab.melden("favicon", "url" to "data:image/png;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP))
  }

  /**
   * Ein neues Fenster wird ein Tab; geöffnet nur nach einem Tippen, wie im
   * Popup-Blocker von Chrome. Die Hilfs-WebView liegt im Profil des Tabs und
   * lädt nichts: ihre erste Navigation nennt nur die Adresse. Öffnet die Seite
   * ein leeres Fenster (`window.open()`), kommt keine Navigation; die
   * Hilfs-WebView fällt dann nach `HILFE_HOECHSTENS_MS`, statt für immer zu
   * bleiben. Was die Seite hineinschreibt, lädt über die Prüfung des Tabs
   * (`Tab.shouldInterceptRequest`, im Emulator gemessen), nicht an ihr vorbei.
   */
  override fun onCreateWindow(view: WebView, dialog: Boolean, tippen: Boolean, ergebnis: Message): Boolean {
    if (!tippen) return false
    val hilfe = WebView(view.context)
    WebViewCompat.setProfile(hilfe, tab.profil)
    // Nicht `post` an der View: eine nie angehängte View führt das nie aus.
    val haupt = Handler(Looper.getMainLooper())
    var weg = false
    val entsorgen = { if (!weg) { weg = true; hilfe.destroy() } }
    hilfe.webViewClient = object : WebViewClient() {
      override fun shouldOverrideUrlLoading(v: WebView, anfrage: WebResourceRequest): Boolean {
        val url = anfrage.url.toString()
        if (url.startsWith("https://") || url.startsWith("http://")) tab.melden("neuer_tab", "url" to url)
        haupt.post(entsorgen)
        return true
      }
    }
    (ergebnis.obj as WebView.WebViewTransport).webView = hilfe
    ergebnis.sendToTarget()
    haupt.postDelayed(entsorgen, HILFE_HOECHSTENS_MS)
    return true
  }

  private fun dialog(art: String, url: String, text: String?, vorgabe: String, antwort: (JSONObject?) -> Unit): Boolean {
    val nr = Rueckfragen.neu(tab.id, antwort)
    tab.melden("dialog", "nr" to nr, "dialog" to art, "herkunft" to url, "text" to text.orEmpty(), "vorgabe" to vorgabe)
    return true
  }

  private fun einfach(ergebnis: JsResult): (JSONObject?) -> Unit = { a ->
    if (a?.optBoolean("ok") == true) ergebnis.confirm() else ergebnis.cancel()
  }

  override fun onJsAlert(view: WebView, url: String, text: String?, ergebnis: JsResult) =
    dialog("alert", url, text, "", einfach(ergebnis))

  override fun onJsConfirm(view: WebView, url: String, text: String?, ergebnis: JsResult) =
    dialog("confirm", url, text, "", einfach(ergebnis))

  override fun onJsBeforeUnload(view: WebView, url: String, text: String?, ergebnis: JsResult) =
    dialog("beforeunload", url, text, "", einfach(ergebnis))

  override fun onJsPrompt(view: WebView, url: String, text: String?, vorgabe: String?, ergebnis: JsPromptResult) =
    dialog("prompt", url, text, vorgabe.orEmpty()) { a ->
      if (a?.optBoolean("ok") == true) ergebnis.confirm(if (a.isNull("text")) "" else a.getString("text")) else ergebnis.cancel()
    }

  override fun onPermissionRequest(anfrage: PermissionRequest) {
    val ressourcen = anfrage.resources
    val herkunft = anfrage.origin?.toString().orEmpty()
    if (ressourcen == null || ressourcen.isEmpty() || !RechteRegeln.erlaubteHerkunft(herkunft)) {
      anfrage.deny()
      return
    }
    val video = ressourcen.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
    val audio = ressourcen.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)
    if (!video && !audio) {
      anfrage.deny()
      return
    }
    val recht = if (video) "kamera" else "mikrofon"

    val nr = Rueckfragen.neu(tab.id) { a ->
      if (a?.optBoolean("erlauben") == true) erlauben(anfrage, ressourcen) else denySicher(anfrage)
    }
    tab.melden("recht", "nr" to nr, "recht" to recht, "herkunft" to herkunft)
  }

  /**
   * Erst das System fragen, dann der Seite erlauben. Vorher stand hier
   * `grant()` direkt nach `requestPermissions()`; beim ersten Zugriff lief
   * die Aufnahme deshalb an und sofort wieder aus (bis 10/2026).
   */
  private fun erlauben(anfrage: PermissionRequest, ressourcen: Array<String>) {
    val akt = plugin.aktivitaet()
    val benoetigt = mutableListOf<String>()
    if (ressourcen.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE) &&
      ContextCompat.checkSelfPermission(akt, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED
    ) {
      benoetigt.add(Manifest.permission.CAMERA)
    }
    if (ressourcen.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE) &&
      ContextCompat.checkSelfPermission(akt, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
    ) {
      benoetigt.add(Manifest.permission.RECORD_AUDIO)
    }
    if (benoetigt.isEmpty()) {
      grantSicher(anfrage, ressourcen)
      return
    }
    val main = akt as? MainActivity
    if (main == null) {
      denySicher(anfrage)
      return
    }

    val nr = naechste++
    wartend[nr] = tab.id to Wartet(anfrage, ressourcen)
    try {
      main.seitenrechteErfragen(benoetigt.toTypedArray()) { alle ->
        val wartet = wartend.remove(nr)?.second ?: return@seitenrechteErfragen
        if (wartet.weg) return@seitenrechteErfragen
        if (alle) grantSicher(wartet.anfrage, wartet.ressourcen) else denySicher(wartet.anfrage)
      }
    } catch (e: Exception) {
      wartend.remove(nr)
      denySicher(anfrage)
    }
  }

  private fun grantSicher(anfrage: PermissionRequest, ressourcen: Array<String>) {
    runCatching { anfrage.grant(ressourcen) }
  }

  private fun denySicher(anfrage: PermissionRequest) {
    runCatching { anfrage.deny() }
  }

  override fun onGeolocationPermissionsShowPrompt(herkunft: String?, rueckruf: GeolocationPermissions.Callback) {
    rueckruf.invoke(herkunft, false, false)
  }

  override fun onShowCustomView(ansicht: View, rueckruf: CustomViewCallback) {
    plugin.vollbildZeigen(ansicht, rueckruf, tab.webView.url)
  }

  override fun onHideCustomView() {
    plugin.vollbildBeenden()
  }

  override fun onShowFileChooser(view: WebView, rueckruf: ValueCallback<Array<Uri>>, wahl: FileChooserParams): Boolean {
    plugin.dateiWaehlen(wahl.createIntent()) { code, daten ->
      rueckruf.onReceiveValue(FileChooserParams.parseResult(code, daten))
    }
    return true
  }
}
