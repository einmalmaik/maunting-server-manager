package com.mauntingstudios.secure_browser

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.widget.RemoteViews

/**
 * Das Wetter-Widget auf dem Startbildschirm. Es zeichnet nur die Werte, die
 * `WetterWorker` zuletzt geschrieben hat; der Worker läuft auch bei
 * geschlossener App und holt die Vorhersage ausschließlich über MSM.
 */
class WetterWidget : AppWidgetProvider() {
  override fun onUpdate(context: Context, verwalter: AppWidgetManager, kennungen: IntArray) {
    zeichnen(context, verwalter, kennungen)
    WetterWorker.planen(context)
  }

  override fun onEnabled(context: Context) {
    WetterWorker.planen(context)
  }

  override fun onDisabled(context: Context) {
    WetterWorker.abbrechen(context)
  }

  companion object {
    const val ABLAGE = "wetter_widget"

    fun zeichnenAlle(context: Context) {
      val verwalter = AppWidgetManager.getInstance(context)
      zeichnen(context, verwalter, verwalter.getAppWidgetIds(ComponentName(context, WetterWidget::class.java)))
    }

    fun lage(context: Context): String {
      val verwalter = AppWidgetManager.getInstance(context)
      return when {
        verwalter.getAppWidgetIds(ComponentName(context, WetterWidget::class.java)).isNotEmpty() -> "liegt"
        verwalter.isRequestPinAppWidgetSupported -> "anheftbar"
        else -> "nein"
      }
    }

    fun anheften(context: Context): Boolean {
      val verwalter = AppWidgetManager.getInstance(context)
      return verwalter.isRequestPinAppWidgetSupported &&
        verwalter.requestPinAppWidget(ComponentName(context, WetterWidget::class.java), null, null)
    }

    private fun zeichnen(context: Context, verwalter: AppWidgetManager, kennungen: IntArray) {
      if (kennungen.isEmpty()) return
      val ablage = context.getSharedPreferences(ABLAGE, Context.MODE_PRIVATE)
      val ort = ablage.getString("ort", null)
      val temp = ablage.getString("temp", null)
      val lage = ablage.getString("lage", "")
      val bereit = ort != null && temp != null
      kennungen.forEach { kennung ->
        val ansicht = RemoteViews(context.packageName, R.layout.widget_wetter).apply {
          setTextViewText(R.id.wetter_ort, ort ?: context.getString(R.string.widget_wetter_name))
          setTextViewText(R.id.wetter_temp, temp ?: "–")
          setTextViewText(R.id.wetter_lage, if (bereit) lage else context.getString(R.string.widget_wetter_nicht_konfiguriert))
          setTextViewText(R.id.wetter_quelle, context.getString(R.string.widget_wetter_quelle))
          setOnClickPendingIntent(R.id.wetter_flaeche, start(context))
        }
        verwalter.updateAppWidget(kennung, ansicht)
      }
    }

    private fun start(context: Context): PendingIntent {
      val absicht = Intent(context, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
      return PendingIntent.getActivity(
        context,
        0,
        absicht,
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
      )
    }
  }
}
