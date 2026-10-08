package com.mauntingstudios.secure_browser

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews

/**
 * Das Such-Widget auf dem Startbildschirm: Suchfeld, Mikrofon und Kamera.
 * Jeder Teil öffnet `WidgetActivity` mit seiner Aktion. Die Kamera steht nur
 * da, wenn die gewählte Suchmaschine Bilder sucht; das meldet die Oberfläche
 * bei jedem Wechsel (`TabsPlugin.widgetStand`).
 */
class SuchWidget : AppWidgetProvider() {
  override fun onUpdate(context: Context, verwalter: AppWidgetManager, kennungen: IntArray) {
    zeichnen(context, verwalter, kennungen)
  }

  companion object {
    private const val ABLAGE = "widget"
    private const val BILDSUCHE = "bildsuche"

    /** Merkt sich, ob die Suchmaschine Bilder sucht, und zeichnet alle Widgets neu. */
    fun bildsucheSetzen(context: Context, an: Boolean) {
      val ablage = context.getSharedPreferences(ABLAGE, Context.MODE_PRIVATE)
      if (ablage.getBoolean(BILDSUCHE, false) == an && ablage.contains(BILDSUCHE)) return
      ablage.edit().putBoolean(BILDSUCHE, an).apply()
      val verwalter = AppWidgetManager.getInstance(context)
      zeichnen(context, verwalter, verwalter.getAppWidgetIds(ComponentName(context, SuchWidget::class.java)))
    }

    /**
     * `liegt`: schon auf dem Startbildschirm. `anheftbar`: der Startbildschirm
     * nimmt es auf Bitte auf. `nein`: geht nur von Hand.
     */
    fun lage(context: Context): String {
      val verwalter = AppWidgetManager.getInstance(context)
      return when {
        verwalter.getAppWidgetIds(ComponentName(context, SuchWidget::class.java)).isNotEmpty() -> "liegt"
        verwalter.isRequestPinAppWidgetSupported -> "anheftbar"
        else -> "nein"
      }
    }

    /**
     * Bittet den Startbildschirm, das Widget aufzunehmen; er fragt selbst
     * nach. Eine andere Suchleiste (Google beim Pixel) kann keine App
     * entfernen, das Widget kommt dazu.
     */
    fun anheften(context: Context): Boolean {
      val verwalter = AppWidgetManager.getInstance(context)
      return verwalter.isRequestPinAppWidgetSupported &&
        verwalter.requestPinAppWidget(ComponentName(context, SuchWidget::class.java), null, null)
    }

    private fun zeichnen(context: Context, verwalter: AppWidgetManager, kennungen: IntArray) {
      val bildsuche = context.getSharedPreferences(ABLAGE, Context.MODE_PRIVATE).getBoolean(BILDSUCHE, false)
      val ansicht = RemoteViews(context.packageName, R.layout.widget_suche).apply {
        setOnClickPendingIntent(R.id.widget_feld, anstoss(context, WidgetActivity.SUCHE))
        setOnClickPendingIntent(R.id.widget_sprache, anstoss(context, WidgetActivity.SPRACHE))
        setOnClickPendingIntent(R.id.widget_kamera, anstoss(context, WidgetActivity.KAMERA))
        setViewVisibility(R.id.widget_kamera, if (bildsuche) View.VISIBLE else View.GONE)
      }
      verwalter.updateAppWidget(kennungen, ansicht)
    }

    private fun anstoss(context: Context, aktion: String): PendingIntent {
      val absicht = Intent(context, WidgetActivity::class.java).setAction(aktion)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
      return PendingIntent.getActivity(context, aktion.hashCode(), absicht, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
  }
}
