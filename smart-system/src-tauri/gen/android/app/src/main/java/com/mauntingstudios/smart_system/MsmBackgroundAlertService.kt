package com.mauntingstudios.smart_system

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.ServiceInfo
import android.graphics.BitmapFactory
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * Hintergrunddienst für zuverlässige Push- & Alarm-Benachrichtigungen außerhalb der App.
 * 
 * - Läuft im Hintergrund (auch bei geschlossener App und nach Geräteneustart)
 * - Fragt periodisch fällige Kalendertermine und Server-Vorfälle ab
 * - Löst native Heads-up Pop-up Benachrichtigungen in der Android-Statusleiste aus
 */
class MsmBackgroundAlertService : Service() {

    private var isRunning = false
    private var workerThread: Thread? = null
    private val prefs: SharedPreferences by lazy {
        getSharedPreferences("msm_background_alerts", Context.MODE_PRIVATE)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!isRunning) {
            isRunning = true
            startForegroundNotification()
            startPollingLoop()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        isRunning = false
        workerThread?.interrupt()
        super.onDestroy()
    }

    private fun ensureChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

            val statusChannel = NotificationChannel(
                CHANNEL_STATUS,
                "Hintergrunddienst",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "MSS Hintergrunddienst"
                setShowBadge(false)
            }

            val alertChannel = NotificationChannel(
                CHANNEL_ALERTS,
                "Benachrichtigungen & Alarme",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Erinnerungen für Termine und Server-Vorfälle"
                setShowBadge(true)
                enableVibration(true)
                enableLights(true)
                lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            }

            nm.createNotificationChannel(statusChannel)
            nm.createNotificationChannel(alertChannel)
        }
    }

    private fun startForegroundNotification() {
        try {
            val pendingIntent = PendingIntent.getActivity(
                this,
                0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )

            val largeIconBitmap = try {
                BitmapFactory.decodeResource(resources, R.mipmap.ic_launcher)
            } catch (e: Exception) {
                null
            }

            val notification = NotificationCompat.Builder(this, CHANNEL_STATUS)
                .setSmallIcon(R.mipmap.ic_launcher_foreground)
                .apply {
                    if (largeIconBitmap != null) {
                        setLargeIcon(largeIconBitmap)
                    }
                }
                .setContentTitle("Hintergrunddienst aktiv")
                .setContentText("Hintergrunddienst ist aktiv")
                .setPriority(NotificationCompat.PRIORITY_LOW)
                .setContentIntent(pendingIntent)
                .setOngoing(true)
                .build()

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID_FOREGROUND,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
                )
            } else {
                startForeground(NOTIFICATION_ID_FOREGROUND, notification)
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun startPollingLoop() {
        workerThread = thread(start = true, isDaemon = true, name = "MsmAlertPoller") {
            while (isRunning) {
                try {
                    pollAlertsAndReminders()
                } catch (e: InterruptedException) {
                    break
                } catch (e: Exception) {
                    // Stiller Hintergrundfehler
                }

                try {
                    Thread.sleep(POLL_INTERVAL_MS)
                } catch (e: InterruptedException) {
                    break
                }
            }
        }
    }

    private fun pollAlertsAndReminders() {
        if (!isBackgroundAlertsEnabled()) return
        val backendUrl = getStoredBackendUrl() ?: return
        val authToken = getStoredAuthToken()

        // 1. Kalender-Erinnerungen prüfen
        try {
            val remindersJson = httpGet("$backendUrl/api/calendar/due-reminders", authToken)
            if (remindersJson != null) {
                val array = JSONArray(remindersJson)
                for (i in 0 until array.length()) {
                    val item = array.getJSONObject(i)
                    val key = item.optString("key", "")
                    if (key.isNotEmpty() && !isKeySeen(key)) {
                        markKeySeen(key)

                        val title = item.optString("title", "Termin")
                        val start = item.optString("start", "")
                        val timeHint = item.optString("time_hint", "")

                        val timeTitle = if (timeHint.isNotEmpty()) "📅 Terminerinnerung ($timeHint)" else "📅 Terminerinnerung"
                        postAlertNotification(
                            notificationId = key.hashCode(),
                            title = timeTitle,
                            text = "$title am $start"
                        )
                    }
                }
            }
        } catch (e: Exception) {
            // Ignorieren
        }

        // 2. Server-Vorfälle & Guardian-Alarme prüfen
        try {
            val incidentsJson = httpGet("$backendUrl/api/system/incident-alerts", authToken)
            if (incidentsJson != null) {
                val array = JSONArray(incidentsJson)
                for (i in 0 until array.length()) {
                    val item = array.getJSONObject(i)
                    val uuid = item.optString("uuid", "")
                    if (uuid.isNotEmpty() && !isKeySeen("inc_$uuid")) {
                        markKeySeen("inc_$uuid")

                        val serverName = item.optString("server_name", "Server")
                        val title = item.optString("title", "Vorfall gemeldet")
                        val desc = item.optString("description", "")

                        postAlertNotification(
                            notificationId = uuid.hashCode(),
                            title = "⚠️ Server-Vorfall: $serverName",
                            text = if (desc.isNotEmpty()) "$title - $desc" else title
                        )
                    }
                }
            }
        } catch (e: Exception) {
            // Ignorieren
        }
    }

    private fun postAlertNotification(notificationId: Int, title: String, text: String) {
        val launchIntent = PendingIntent.getActivity(
            this,
            notificationId,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val largeIconBitmap = try {
            BitmapFactory.decodeResource(resources, R.mipmap.ic_launcher)
        } catch (e: Exception) {
            null
        }

        val notification = NotificationCompat.Builder(this, CHANNEL_ALERTS)
            .setSmallIcon(R.mipmap.ic_launcher_foreground)
            .apply {
                if (largeIconBitmap != null) {
                    setLargeIcon(largeIconBitmap)
                }
            }
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setDefaults(NotificationCompat.DEFAULT_ALL)
            .setAutoCancel(true)
            .setContentIntent(launchIntent)
            .build()

        try {
            NotificationManagerCompat.from(this).notify(notificationId, notification)
        } catch (e: SecurityException) {
            // Keine POST_NOTIFICATIONS Berechtigung
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun httpGet(urlStr: String, token: String?): String? {
        val url = URL(urlStr)
        val conn = url.openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        if (!token.isNullOrEmpty()) {
            conn.setRequestProperty("Authorization", "Bearer $token")
        }

        val code = conn.responseCode
        if (code in 200..299) {
            val reader = BufferedReader(InputStreamReader(conn.inputStream))
            val sb = StringBuilder()
            var line: String? = reader.readLine()
            while (line != null) {
                sb.append(line)
                line = reader.readLine()
            }
            reader.close()
            return sb.toString()
        }
        return null
    }

    private fun getStoredBackendUrl(): String? {
        try {
            val file = File(filesDir, "konfig.json")
            if (file.exists()) {
                val json = JSONObject(file.readText())
                val url = json.optString("backend_url", "")
                if (url.isNotEmpty()) return url.trimEnd('/')
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
        return null
    }

    private fun getStoredAuthToken(): String? {
        try {
            val file = File(filesDir, ".session_auth")
            if (file.exists()) {
                val token = file.readText().trim()
                if (token.isNotEmpty()) return token
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
        return null
    }

    private fun isBackgroundAlertsEnabled(): Boolean {
        try {
            val file = File(filesDir, "konfig.json")
            if (file.exists()) {
                val json = JSONObject(file.readText())
                if (json.has("autostart_aktiv")) {
                    return json.getBoolean("autostart_aktiv")
                }
            }
        } catch (e: Exception) {
            e.printStackTrace()
        }
        return true
    }

    private fun isKeySeen(key: String): Boolean {
        return prefs.getBoolean("seen_$key", false)
    }

    private fun markKeySeen(key: String) {
        prefs.edit().putBoolean("seen_$key", true).apply()
    }

    companion object {
        const val CHANNEL_STATUS = "mss_status"
        const val CHANNEL_ALERTS = "mss_alerts"
        const val NOTIFICATION_ID_FOREGROUND = 1001
        const val POLL_INTERVAL_MS = 25_000L

        fun start(context: Context) {
            try {
                val intent = Intent(context, MsmBackgroundAlertService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }
}
