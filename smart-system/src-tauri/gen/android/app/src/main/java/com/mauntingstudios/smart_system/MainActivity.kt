package com.mauntingstudios.smart_system

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.ActivityInfo
import android.os.Build
import android.os.Bundle
import androidx.activity.enableEdgeToEdge

class MainActivity : TauriActivity() {
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

