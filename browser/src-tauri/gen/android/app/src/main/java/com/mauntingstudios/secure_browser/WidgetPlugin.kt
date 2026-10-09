package com.mauntingstudios.secure_browser

import android.app.Activity
import android.app.role.RoleManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.view.inputmethod.InputMethodManager
import android.webkit.WebView
import androidx.activity.result.ActivityResult
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import org.json.JSONObject

@InvokeArg class WidgetArgs { var bildsuche = false }

/**
 * Was den Browser von außen startet (`src/widget.rs`): das Such-Widget und
 * Links aus anderen Apps. Dazu, ob das Widget auf dem Startbildschirm liegt
 * und die Kamera zeigt, und ob der Browser der Standardbrowser ist. Das Foto
 * selbst schickt der Tab ab (`TabsPlugin.bildsuche`).
 */
@TauriPlugin
class WidgetPlugin(private val activity: Activity) : Plugin(activity) {
  private var oberflaeche: WebView? = null

  override fun load(webView: WebView) {
    oberflaeche = webView
    linkAufnehmen(activity.intent)
  }

  /**
   * Ein Link aus einer anderen App (`ACTION_VIEW`, Manifest). Nur `http` und
   * `https`; ob ein Tab die Adresse laden darf, prüft Rust beim Laden. Der
   * Intent verliert seine Aktion, damit ein Neuaufbau der Activity den Link
   * nicht noch einmal öffnet.
   */
  private fun linkAufnehmen(intent: Intent?): Boolean {
    if (intent?.action != Intent.ACTION_VIEW) return false
    val url = intent.dataString
    val schema = intent.data?.scheme?.lowercase()
    intent.action = null
    if (url == null || url.length > LINK_HOECHSTENS || (schema != "http" && schema != "https")) return false
    WidgetActivity.wartend = JSONObject().put("art", "link").put("url", url)
    return true
  }

  /** Was das Widget oder ein Link angestoßen hat (`WidgetActivity.wartend`), einmal. */
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
    linkAufnehmen(intent)
    if (WidgetActivity.wartend != null) TabsBruecke.widget()
  }

  @Command
  fun standard(invoke: Invoke) {
    aufUi(activity, invoke) { JSObject().put("standard", istStandard()) }
  }

  /**
   * Fragt per Android-Dialog, ob der Browser Standard wird. Ab der zweiten
   * Ablehnung zeigt Android den Dialog nicht mehr; dann bleiben die
   * Einstellungen ([einstellungen]).
   */
  @Command
  fun standardWerden(invoke: Invoke) {
    val rollen = if (Build.VERSION.SDK_INT >= 29) activity.getSystemService(RoleManager::class.java) else null
    if (rollen == null || !rollen.isRoleAvailable(RoleManager.ROLE_BROWSER)) {
      einstellungen(invoke)
      return
    }
    startActivityForResult(invoke, rollen.createRequestRoleIntent(RoleManager.ROLE_BROWSER), "nachStandard")
  }

  @ActivityCallback
  private fun nachStandard(invoke: Invoke, ergebnis: ActivityResult) {
    invoke.resolve(JSObject().put("standard", istStandard()))
  }

  /** Die Standard-Apps in den Android-Einstellungen. */
  @Command
  fun einstellungen(invoke: Invoke) {
    aufUi(activity, invoke) {
      activity.startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS))
      JSObject().put("standard", istStandard())
    }
  }

  private fun istStandard(): Boolean {
    if (Build.VERSION.SDK_INT >= 29) {
      return activity.getSystemService(RoleManager::class.java).isRoleHeld(RoleManager.ROLE_BROWSER)
    }
    val wer = activity.packageManager.resolveActivity(
      Intent(Intent.ACTION_VIEW, Uri.parse("https://example.com/")), PackageManager.MATCH_DEFAULT_ONLY,
    )
    return wer?.activityInfo?.packageName == activity.packageName
  }

  private companion object {
    /** Länger ist keine Adresse, die jemand teilt; die Oberfläche speichert ohnehin gekürzt. */
    const val LINK_HOECHSTENS = 32 * 1024
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
