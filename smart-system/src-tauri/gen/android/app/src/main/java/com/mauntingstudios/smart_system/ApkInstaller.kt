package com.mauntingstudios.smart_system

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.net.Uri
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.FileProvider
import java.io.File
import java.io.FileInputStream

/**
 * Zuverlässiger APK-Installer für Android:
 * 1. Primär: Android PackageInstaller.Session mit USER_ACTION_NOT_REQUIRED (ab Android 12)
 *    ermöglicht vollautomatische Hintergrund-Updates ohne Nutzerinteraktion für Self-Updates.
 * 2. Sekundär / Fallback: FileProvider-Intent mit content:// URI (Android 7-14+ sicher,
 *    verhindert FileUriExposedException).
 */
object ApkInstaller {
    private const val TAG = "MsmApkInstaller"
    const val ACTION_INSTALL_STATUS = "com.mauntingstudios.smart_system.INSTALL_STATUS"
    const val NOTIFICATION_ID_FALLBACK = 1004

    fun installApk(context: Context, apkPath: String): Boolean {
        val apkFile = File(apkPath)
        if (!apkFile.exists() || !apkFile.canRead()) {
            Log.e(TAG, "APK-Datei existiert nicht oder ist nicht lesbar: $apkPath")
            return false
        }


        // 1. Primär: PackageInstaller.Session
        try {
            val packageInstaller = context.packageManager.packageInstaller
            val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
                setAppPackageName(context.packageName)
                setSize(apkFile.length())
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
                }
                if (Build.VERSION.SDK_INT >= 34) {
                    setRequestUpdateOwnership(false)
                }
            }

            val sessionId = packageInstaller.createSession(params)
            val session = packageInstaller.openSession(sessionId)

            FileInputStream(apkFile).use { input ->
                session.openWrite("package_apk", 0, apkFile.length()).use { output ->
                    input.copyTo(output)
                    session.fsync(output)
                }
            }

            val intent = Intent(context, InstallStatusReceiver::class.java).apply {
                action = ACTION_INSTALL_STATUS
                putExtra("apk_path", apkPath)
            }

            val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }

            val pendingIntent = PendingIntent.getBroadcast(
                context,
                sessionId,
                intent,
                flags
            )

            session.commit(pendingIntent.intentSender)
            session.close()
            Log.i(TAG, "PackageInstaller Session $sessionId erfolgreich initiiert.")
            return true
        } catch (e: Exception) {
            Log.w(TAG, "PackageInstaller fehlgeschlagen, weiche auf FileProvider aus: ${e.message}")
        }

        // 2. Sekundär: FileProvider Fallback
        return installViaFileProvider(context, apkFile)
    }

    fun installViaFileProvider(context: Context, apkFile: File): Boolean {
        return try {
            val contentUri: Uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                apkFile
            )

            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(contentUri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
            }

            try {
                context.startActivity(intent)
                Log.i(TAG, "FileProvider Installation gestartet für: ${apkFile.name}")
                true
            } catch (e: Exception) {
                Log.w(TAG, "Direkter Start fehlgeschlagen (Hintergrund-Beschränkung), zeige Notification: ${e.message}")
                showInstallNotification(context, intent)
                true
            }
        } catch (e: Exception) {
            Log.e(TAG, "FileProvider Installation fehlgeschlagen: ${e.message}", e)
            false
        }
    }

    fun showInstallNotification(context: Context, intent: Intent) {
        try {
            val pendingIntent = PendingIntent.getActivity(
                context,
                NOTIFICATION_ID_FALLBACK,
                intent,
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
                } else {
                    PendingIntent.FLAG_UPDATE_CURRENT
                }
            )

            val notification = NotificationCompat.Builder(context, MsmBackgroundAlertService.CHANNEL_ALERTS)
                .setSmallIcon(R.mipmap.ic_launcher_foreground)
                .setContentTitle("MSS Update bereit")
                .setContentText("Tippe hier, um die Aktualisierung abzuschließen.")
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setContentIntent(pendingIntent)
                .setAutoCancel(true)
                .build()

            NotificationManagerCompat.from(context).notify(NOTIFICATION_ID_FALLBACK, notification)
        } catch (e: Exception) {
            Log.e(TAG, "Konnte Fallback-Notification nicht anzeigen: ${e.message}")
        }
    }
}
