package com.mauntingstudios.smart_system

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import java.io.File

/**
 * BroadcastReceiver zur Auswertung von PackageInstaller-Statusmeldungen.
 * - Bei USER_ACTION_REQUIRED (z.B. Erstanfrage) wird das Bestätigungs-Intent angezeigt
 *   oder als Heads-Up Notification bereitgestellt, falls die App im Hintergrund ist.
 * - Bei Erfolg werden eventuelle Update-Notifications aufgeräumt.
 * - Bei Fehlern erfolgt ein automatischer Fallback auf den FileProvider-Installationsdialog.
 */
class InstallStatusReceiver : BroadcastReceiver() {
    companion object {
        const val NOTIFICATION_ID_CONFIRM = 1003
    }

    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        val message = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
        val apkPath = intent.getStringExtra("apk_path")

        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                Log.i("InstallStatusReceiver", "Benutzerbestätigung erforderlich (STATUS_PENDING_USER_ACTION).")
                val confirmIntent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                if (confirmIntent != null) {
                    confirmIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    var directLaunchSucceeded = false
                    try {
                        context.startActivity(confirmIntent)
                        directLaunchSucceeded = true
                    } catch (e: Exception) {
                        Log.w("InstallStatusReceiver", "Direkter Start fehlgeschlagen: ${e.message}")
                    }

                    // Falls direkter Start im Hintergrund blockiert wurde, via Notification anbieten:
                    if (!directLaunchSucceeded) {
                        showConfirmNotification(context, confirmIntent)
                    }
                }
            }
            PackageInstaller.STATUS_SUCCESS -> {
                Log.i("InstallStatusReceiver", "Update via PackageInstaller erfolgreich abgeschlossen!")
                try {
                    NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID_CONFIRM)
                    NotificationManagerCompat.from(context).cancel(ApkInstaller.NOTIFICATION_ID_FALLBACK)
                } catch (e: Exception) {
                    // Stille Bereinigung
                }
            }
            else -> {
                Log.w("InstallStatusReceiver", "PackageInstaller Status: $status ($message). Versuche Fallback auf FileProvider...")
                if (!apkPath.isNullOrEmpty()) {
                    val apkFile = File(apkPath)
                    if (apkFile.exists()) {
                        ApkInstaller.installViaFileProvider(context, apkFile)
                    }
                }
            }
        }
    }

    private fun showConfirmNotification(context: Context, confirmIntent: Intent) {
        try {
            val pendingIntent = PendingIntent.getActivity(
                context,
                NOTIFICATION_ID_CONFIRM,
                confirmIntent,
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
                } else {
                    PendingIntent.FLAG_UPDATE_CURRENT
                }
            )

            val notification = NotificationCompat.Builder(context, MsmBackgroundAlertService.CHANNEL_ALERTS)
                .setSmallIcon(R.mipmap.ic_launcher_foreground)
                .setContentTitle("MSS Update bereit")
                .setContentText("Tippe hier, um die Aktualisierung zu bestätigen.")
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .build()

            NotificationManagerCompat.from(context).notify(NOTIFICATION_ID_CONFIRM, notification)
        } catch (e: Exception) {
            Log.e("InstallStatusReceiver", "Konnte Confirm-Notification nicht anzeigen: ${e.message}")
        }
    }
}
