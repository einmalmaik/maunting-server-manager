package com.mauntingstudios.smart_system

import android.app.Activity
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.Plugin

@InvokeArg
class InstallApkArgs {
    lateinit var apkPath: String
}

/**
 * Tauri-Plugin zur Übergabe des APK-Installationsauftrags von Rust an das Android-Betriebssystem.
 */
@TauriPlugin
class ApkInstallerPlugin(private val activity: Activity) : Plugin(activity) {

    @Command
    fun installApk(invoke: Invoke) {
        try {
            val args = invoke.parseArgs(InstallApkArgs::class.java)
            val success = ApkInstaller.installApk(activity, args.apkPath)
            if (success) {
                invoke.resolve()
            } else {
                invoke.reject("Installation konnte nicht initialisiert werden")
            }
        } catch (e: Exception) {
            invoke.reject(e.message ?: "Unbekannter Fehler bei der APK-Installation")
        }
    }
}
