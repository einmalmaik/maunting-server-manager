package com.mauntingstudios.secure_browser

import android.app.Activity
import android.content.Intent
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import org.json.JSONObject

@InvokeArg class WidgetArgs { var bildsuche = false }

/**
 * Das Such-Widget (`src/widget.rs`): was es angestoßen hat, ob es auf dem
 * Startbildschirm liegt, und ob es die Kamera zeigt. Das Foto selbst schickt
 * der Tab ab (`TabsPlugin.bildsuche`).
 */
@TauriPlugin
class WidgetPlugin(private val activity: Activity) : Plugin(activity) {
  private var oberflaeche: WebView? = null

  override fun load(webView: WebView) {
    oberflaeche = webView
  }

  /** Was das Widget angestoßen hat (`WidgetActivity`), einmal. */
  @Command
  fun startAbholen(invoke: Invoke) {
    aufUi(activity, invoke) {
      val start = WidgetActivity.wartend
      WidgetActivity.wartend = null
      JSObject().put("start", start ?: JSONObject.NULL)
    }
  }

  /** Ein neuer Anstoß, während der Browser schon läuft: die Oberfläche holt ihn ab. */
  override fun onNewIntent(intent: Intent) {
    if (WidgetActivity.wartend != null) TabsBruecke.widget()
  }

  /**
   * Die Tastatur für das Feld, das die Oberfläche gerade fokussiert hat. Ein
   * `focus()` aus Skript zeigt sie nicht, wenn kein Tippen vorausging.
   */
  @Command
  fun tastaturZeigen(invoke: Invoke) {
    aufUi(activity, invoke) {
      val ansicht = oberflaeche ?: return@aufUi null
      ansicht.requestFocus()
      activity.getSystemService(InputMethodManager::class.java).showSoftInput(ansicht, InputMethodManager.SHOW_IMPLICIT)
      null
    }
  }

  @Command
  fun lage(invoke: Invoke) {
    aufUi(activity, invoke) { JSObject().put("lage", SuchWidget.lage(activity)) }
  }

  @Command
  fun anheften(invoke: Invoke) {
    aufUi(activity, invoke) { JSObject().put("ok", SuchWidget.anheften(activity)) }
  }

  @Command
  fun stand(invoke: Invoke) {
    val a = invoke.parseArgs(WidgetArgs::class.java)
    aufUi(activity, invoke) {
      SuchWidget.bildsucheSetzen(activity, a.bildsuche)
      // Ohne Bildsuche geht ein wartendes Foto nirgends mehr hin.
      if (!a.bildsuche) WidgetActivity.foto(activity).delete()
      null
    }
  }
}

/**
 * Rust ruft Plugins von einem eigenen Faden; ein Befehl läuft auf dem
 * UI-Faden und antwortet erst dort.
 */
fun aufUi(activity: Activity, invoke: Invoke, tun: () -> JSObject?) {
  activity.runOnUiThread {
    try {
      val antwort = tun()
      if (antwort != null) invoke.resolve(antwort) else invoke.resolve()
    } catch (e: Exception) {
      invoke.reject(e.message ?: e.toString())
    }
  }
}
