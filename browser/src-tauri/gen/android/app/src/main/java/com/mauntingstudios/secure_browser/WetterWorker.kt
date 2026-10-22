package com.mauntingstudios.secure_browser

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * Holt die Wettervorhersage für das Homescreen-Widget, auch wenn die App
 * geschlossen ist. Der Weg ist bewusst schmal:
 *
 * - Die Anfrage geht ausschließlich an die gekoppelte MSM-Adresse.
 * - Sie trägt ein eigenes Widget-Token (nur `POST /api/browser/wetter/widget`),
 *   kein Konto- oder Kopplungs-Token. Das Token liegt im Keystore, nicht im
 *   Klartext in SharedPreferences.
 * - Koordinaten kommen aus der in Pontis gewählten Stadt, nicht aus einer
 *   Geräteortung.
 *
 * Fehler klemmen still: Der letzte Stand bleibt sichtbar, nach einem
 * abgelaufenen Token steht wieder „Ort in Pontis wählen“.
 */
class WetterWorker(appContext: Context, params: WorkerParameters) : CoroutineWorker(appContext, params) {
  override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
    val ablage = applicationContext.getSharedPreferences(WetterWidget.ABLAGE, Context.MODE_PRIVATE)
    val basis = ablage.getString("basis", null)?.trimEnd('/')
    val lat = ablage.getFloat("lat", Float.NaN)
    val lon = ablage.getFloat("lon", Float.NaN)
    if (basis.isNullOrBlank() || lat.isNaN() || lon.isNaN()) return@withContext Result.success()
    if (!basis.startsWith("https://") && !basis.startsWith("http://127.0.0.1") && !basis.startsWith("http://localhost")) {
      return@withContext Result.success()
    }
    val token = try {
      val cipher = Schluesselfach.cipherZumLesen(applicationContext, "widget_wetter_token")
      cipher?.let { Schluesselfach.lies(applicationContext, "widget_wetter_token", it) }
    } catch (_: Exception) {
      null
    }
    if (token.isNullOrBlank()) return@withContext Result.success()

    val verbindung = (URL("$basis/api/browser/wetter/widget").openConnection() as HttpURLConnection).apply {
      requestMethod = "POST"
      connectTimeout = 10_000
      readTimeout = 10_000
      doOutput = true
      setRequestProperty("Content-Type", "application/json")
      setRequestProperty("Authorization", "Bearer $token")
    }
    try {
      val koerper = JSONObject().put("lat", lat.toDouble()).put("lon", lon.toDouble()).toString()
      verbindung.outputStream.use { it.write(koerper.toByteArray(Charsets.UTF_8)) }
      val code = verbindung.responseCode
      if (code == HttpURLConnection.HTTP_UNAUTHORIZED || code == HttpURLConnection.HTTP_FORBIDDEN) {
        // Abgelaufen oder widerrufen: Das Token ist wertlos. Die App stellt
        // beim nächsten Start ein neues aus.
        runCatching { Schluesselfach.loesche(applicationContext, "widget_wetter_token") }
        ablage.edit().remove("temp").remove("lage").apply()
        WetterWidget.zeichnenAlle(applicationContext)
        return@withContext Result.success()
      }
      if (code != HttpURLConnection.HTTP_OK) return@withContext Result.retry()

      val antwort = JSONObject(verbindung.inputStream.bufferedReader(Charsets.UTF_8).readText())
      val jetzt = antwort.getJSONObject("jetzt")
      val celsius = jetzt.getDouble("temp")
      val einheit = ablage.getString("einheit", "celsius")
      val anzeige = if (einheit == "fahrenheit") "${Math.round(celsius * 1.8 + 32)}°F" else "${Math.round(celsius)}°C"
      ablage.edit()
        .putString("temp", anzeige)
        .putString("lage", jetzt.optString("lage", ""))
        .putLong("geholt", System.currentTimeMillis())
        .apply()
      WetterWidget.zeichnenAlle(applicationContext)
      Result.success()
    } catch (_: Exception) {
      Result.retry()
    } finally {
      verbindung.disconnect()
    }
  }

  companion object {
    private const val ARBEIT = "wetter-widget"

    fun planen(context: Context) {
      val anfrage = PeriodicWorkRequestBuilder<WetterWorker>(30, TimeUnit.MINUTES)
        .setConstraints(
          Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build(),
        )
        .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.MINUTES)
        .build()
      WorkManager.getInstance(context).enqueueUniquePeriodicWork(ARBEIT, ExistingPeriodicWorkPolicy.KEEP, anfrage)
    }

    fun abbrechen(context: Context) {
      WorkManager.getInstance(context).cancelUniqueWork(ARBEIT)
    }
  }
}
