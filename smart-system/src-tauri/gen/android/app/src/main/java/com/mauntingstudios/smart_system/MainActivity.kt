package com.mauntingstudios.smart_system

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.ActivityInfo
import android.os.Build
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.enableEdgeToEdge
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

class MainActivity : TauriActivity() {
  /**
   * Höhe der Gesten- bzw. Navigationsleiste in CSS-Pixeln.
   *
   * Die App zeichnet randlos. Ältere Android-WebViews (im Emulator 124) melden
   * die Leiste unten aber nicht als env(safe-area-inset-bottom): Fußleisten
   * lagen unter ihr. Die Oberfläche nimmt das Größere von beiden
   * (--msm-unten-sicher in index.css, lib/gestenleiste.ts).
   */
  @Volatile private var gestenleiste = 0f

  override fun onWebViewCreate(webView: WebView) {
    super.onWebViewCreate(webView)
    // Erst hier: Rust setzt die WebView nach onCreate als Inhalt und überdeckte
    // einen früher gesetzten Hinweis. `post` kommt nach diesem Setzen.
    val kennung = webView.settings.userAgentString.orEmpty()
    if (WebviewPruefung.zuAlt(kennung)) {
      webView.post { zuAltZeigen(WebviewPruefung.chromeVersion(kennung) ?: 0) }
      return
    }
    // Gelesen beim Start der Seite; Änderungen schiebt das Layout nach.
    webView.addJavascriptInterface(object {
      @JavascriptInterface fun unten(): Float = gestenleiste
    }, "MsmRand")
    // Nur lesen, kein eigener OnApplyWindowInsetsListener: der ersetzt den des
    // WebViews, und env(safe-area-inset-top) fiel auf 0, die Statusleiste lag
    // über der Kopfzeile.
    webView.addOnLayoutChangeListener { view, _, _, _, _, _, _, _, _ ->
      val insets = ViewCompat.getRootWindowInsets(view) ?: return@addOnLayoutChangeListener
      val neu = insets.getInsets(WindowInsetsCompat.Type.navigationBars()).bottom / view.resources.displayMetrics.density
      if (neu != gestenleiste) {
        gestenleiste = neu
        view.post { webView.evaluateJavascript("window.__msmGestenleiste?.($neu)", null) }
      }
    }
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    super.onCreate(savedInstanceState)
    createNotificationChannels()
  }

  /** Statt einer weißen Seite: was fehlt und ein Weg dorthin (`WebviewPruefung`). */
  private fun zuAltZeigen(fassung: Int) {
    val rand = (32 * resources.displayMetrics.density).toInt()
    val hinweis = TextView(this).apply {
      text = getString(R.string.webview_zu_alt, fassung, WebviewPruefung.MINDEST)
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
    // Wird die Activity zerstört (Beenden, Wegwischen aus Recents), endet der
    // Prozess ganz: die Tauri/Rust-Umgebung startet beim nächsten Öffnen frisch
    // und ohne Deadlock. Geplante Erinnerungen hält Android selbst
    // (Erinnerungen.kt); ein Wecker startet dafür einen neuen Prozess.
    android.os.Process.killProcess(android.os.Process.myPid())
  }

  private fun createNotificationChannels() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

      // Channel für Terminerinnerungen und Systemmeldungen (HIGH = Banner-Pop-up)
      val alertChannel = NotificationChannel(
        Erinnerungen.KANAL,
        "Benachrichtigungen & Alarme",
        NotificationManager.IMPORTANCE_HIGH
      ).apply {
        description = "Erinnerungen für Termine und Systemmeldungen"
        setShowBadge(true)
        enableVibration(true)
        enableLights(true)
        lockscreenVisibility = Notification.VISIBILITY_PUBLIC
      }

      // Default Channel für Tauri Standard-Plugins
      val defaultChannel = NotificationChannel(
        "default",
        "Allgemeine Benachrichtigungen",
        NotificationManager.IMPORTANCE_HIGH
      ).apply {
        description = "Allgemeine Benachrichtigungen und Pop-up-Hinweise"
        setShowBadge(true)
        enableVibration(true)
        enableLights(true)
        lockscreenVisibility = Notification.VISIBILITY_PUBLIC
      }

      // Kanal des früheren Hintergrunddienstes
      notificationManager.deleteNotificationChannel("mss_status")
      notificationManager.createNotificationChannel(alertChannel)
      notificationManager.createNotificationChannel(defaultChannel)
    }
  }
}

