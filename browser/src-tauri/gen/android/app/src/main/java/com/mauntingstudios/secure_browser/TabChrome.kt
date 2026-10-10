package com.mauntingstudios.secure_browser

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
import androidx.webkit.WebViewCompat
import org.json.JSONObject
import java.io.ByteArrayOutputStream

/**
 * Was die Seite vom Browser will: Titel, Symbol, neue Fenster, Dialoge,
 * Vollbild, Dateiauswahl. Kamera, Mikrofon und Standort lehnt der Tab ab:
 * die App deklariert diese Rechte nicht, und eine Seite bekommt sie nie
 * (ehrliche Lücke gegenüber Windows).
 */
class TabChrome(private val plugin: TabsPlugin, private val tab: Tab) : WebChromeClient() {
  private companion object {
    /** Länger lebt ein Fenster nie, das keine Adresse nennt. */
    const val HILFE_HOECHSTENS_MS = 10_000L
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
    anfrage.deny()
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
