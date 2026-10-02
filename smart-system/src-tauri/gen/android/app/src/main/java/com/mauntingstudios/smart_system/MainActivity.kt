package com.mauntingstudios.smart_system

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ActivityInfo
import android.os.Build
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView
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
    MsmBackgroundAlertService.start(this)
  }

  override fun onDestroy() {
    super.onDestroy()
    // Wenn die UI-Activity zerstört wird (z. B. Beenden oder Wegwischen aus Recents),
    // beenden wir den UI-Prozess vollständig. Da der MsmBackgroundAlertService
    // im separaten Prozess ":alert_service" läuft, bleibt der Hintergrund-Alarmdienst
    // davon unberührt aktiv, während die native Tauri/Rust-Umgebung beim nächsten Start
    // in einem sauberen, frischen Prozess ohne Deadlock startet.
    android.os.Process.killProcess(android.os.Process.myPid())
  }

  private fun createNotificationChannels() {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

      // 1. Channel für Status & Hintergrunddienst (unaufdringlich)
      val statusChannel = NotificationChannel(
        "mss_status",
        "Hintergrunddienst",
        NotificationManager.IMPORTANCE_LOW
      ).apply {
        description = "Status des MSS Hintergrunddienstes"
        setShowBadge(false)
      }

      // 2. Channel für Terminerinnerungen & Server-Alarme (HIGH = Banner-Pop-up)
      val alertChannel = NotificationChannel(
        "mss_alerts",
        "Benachrichtigungen & Alarme",
        NotificationManager.IMPORTANCE_HIGH
      ).apply {
        description = "Erinnerungen für Termine, Server-Vorfälle und Systemmeldungen"
        setShowBadge(true)
        enableVibration(true)
        enableLights(true)
        lockscreenVisibility = Notification.VISIBILITY_PUBLIC
      }

      // 4. Default Channel für Tauri Standard-Plugins
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

      notificationManager.createNotificationChannel(statusChannel)
      notificationManager.createNotificationChannel(alertChannel)
      notificationManager.createNotificationChannel(defaultChannel)
    }
  }
}

