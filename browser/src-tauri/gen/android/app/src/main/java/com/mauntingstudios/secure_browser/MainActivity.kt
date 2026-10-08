package com.mauntingstudios.secure_browser

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.graphics.Color
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewFeature

class MainActivity : TauriActivity() {
  /**
   * Ränder in CSS-Pixeln, die ältere WebViews nicht als env(safe-area-inset-*)
   * melden: unten die Gesten- bzw. Navigationsleiste, oben im Querformat die
   * Statusleiste, seitlich Kamera-Ausschnitt und Navigationsleiste. Die
   * Oberfläche nimmt das Größere von beiden (`--msm-*-sicher`,
   * lib/gestenleiste.ts, Punkt 87).
   */
  @Volatile private var rand = mapOf("oben" to 0f, "links" to 0f, "rechts" to 0f, "unten" to 0f)

  override fun onCreate(savedInstanceState: Bundle?) {
    // Die Oberfläche ist immer dunkel (`browser.html`): helle Symbole in
    // Status- und Gestenleiste, sonst stünden Uhr und Akku dunkel auf dunkel.
    enableEdgeToEdge(SystemBarStyle.dark(Color.TRANSPARENT), SystemBarStyle.dark(Color.TRANSPARENT))
    super.onCreate(savedInstanceState)
  }

  override fun onWebViewCreate(webView: WebView) {
    super.onWebViewCreate(webView)
    // Erst hier: Rust setzt die WebView nach onCreate als Inhalt und überdeckte
    // einen früher gesetzten Hinweis. `post` kommt nach diesem Setzen.
    val kennung = webView.settings.userAgentString.orEmpty()
    if (WebviewPruefung.zuAlt(kennung, funktionenDa())) {
      webView.post { zuAltZeigen(WebviewPruefung.chromeVersion(kennung) ?: 0) }
      return
    }
    webView.addJavascriptInterface(object {
      @JavascriptInterface fun unten(): Float = rand.getValue("unten")
      @JavascriptInterface fun oben(): Float = rand.getValue("oben")
      @JavascriptInterface fun links(): Float = rand.getValue("links")
      @JavascriptInterface fun rechts(): Float = rand.getValue("rechts")
    }, "MsmRand")
    // Nur lesen, kein eigener OnApplyWindowInsetsListener: der ersetzt den des
    // WebViews, und env(safe-area-inset-top) fiel auf 0 (Punkt 87).
    webView.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
      val insets = ViewCompat.getRootWindowInsets(view) ?: return@addOnLayoutChangeListener
      val d = view.resources.displayMetrics.density
      val leisten = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
      val neu = mapOf(
        "oben" to leisten.top / d,
        "links" to leisten.left / d,
        "rechts" to leisten.right / d,
        "unten" to insets.getInsets(WindowInsetsCompat.Type.navigationBars()).bottom / d,
      )
      if (neu == rand) return@addOnLayoutChangeListener
      rand = neu
      val skript = neu.entries.joinToString(";") { (seite, px) ->
        if (seite == "unten") "window.__msmGestenleiste?.($px)" else "window.__msmRand?.('$seite',$px)"
      }
      view.post { webView.evaluateJavascript(skript, null) }
    }
  }

  private fun funktionenDa(): Boolean =
    WebViewFeature.isFeatureSupported(WebViewFeature.MULTI_PROFILE) &&
      WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT) &&
      WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)

  /** Statt eines Browsers, der Seiten an den Speicher der App ließe (`WebviewPruefung`). */
  private fun zuAltZeigen(fassung: Int) {
    val rand = (32 * resources.displayMetrics.density).toInt()
    val hinweis = TextView(this).apply {
      text = getString(R.string.webview_zu_alt, fassung)
      textSize = 17f
      setPadding(0, 0, 0, rand)
    }
    val knopf = Button(this).apply {
      text = getString(R.string.webview_aktualisieren)
      setOnClickListener { webviewAktualisieren() }
    }
    setContentView(LinearLayout(this).apply {
      orientation = LinearLayout.VERTICAL
      gravity = Gravity.CENTER
      setPadding(rand, rand, rand, rand)
      addView(hinweis)
      addView(knopf)
    })
  }

  private fun webviewAktualisieren() {
    val paket = "com.google.android.webview"
    try {
      startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$paket")))
    } catch (e: ActivityNotFoundException) {
      try {
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=$paket")))
      } catch (e: ActivityNotFoundException) {
        // Weder Store noch Browser: der Text sagt, was zu tun ist.
      }
    }
  }

  override fun onDestroy() {
    super.onDestroy()
    // Wie im Smart System: endet die Activity, endet der Prozess, und die
    // Tauri-/Rust-Umgebung startet beim nächsten Öffnen frisch.
    android.os.Process.killProcess(android.os.Process.myPid())
  }
}
