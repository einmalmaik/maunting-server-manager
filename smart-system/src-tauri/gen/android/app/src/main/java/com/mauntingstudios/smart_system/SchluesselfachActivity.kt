package com.mauntingstudios.smart_system

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.security.keystore.KeyPermanentlyInvalidatedException
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat

/**
 * Die Abfrage vor dem Fach.
 *
 * Eine eigene Activity, weil `BiometricPrompt` eine `FragmentActivity` braucht
 * und die Activity, in der die Anwendung läuft, keine ist. Denselben Weg geht
 * `tauri-plugin-biometric` für seine reine Bestätigung.
 *
 * Anders als dort wird hier nicht nur bestätigt, sondern auch gerechnet: der
 * `Cipher` geht als `CryptoObject` in die Abfrage hinein und darf erst danach
 * benutzt werden. Das ist der Unterschied zwischen „der Keystore hat den
 * Fingerabdruck gesehen" und „der Keystore hat den Schlüssel freigegeben". Eine
 * Bestätigung, deren Ergebnis nur ein `true` ist, lässt sich aushebeln; ein
 * Schlüssel, den die Hardware nicht herausrückt, nicht.
 *
 * Das Ergebnis geht über [antwort] zurück, nicht über `setResult`. Der Grund ist
 * der Inhalt: durch dieses Fach geht der Messenger-PIN und das Master-Passwort
 * des Tresors. Ein Intent-Ergebnis reist über den system_server; ein Feld im
 * eigenen Prozess nicht. Für einen Rückgabewert dieser Art ist der kurze Weg
 * der richtige.
 */
class SchluesselfachActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_FACH = "fach"
        const val EXTRA_MODUS = "modus"
        const val EXTRA_GEHEIMNIS = "geheimnis"
        const val EXTRA_NACHRICHT = "nachricht"

        const val MODUS_ABLEGEN = "ablegen"
        const val MODUS_LESEN = "lesen"

        /**
         * Das Übergabefach für den Rückkanal. Die Activity nimmt ihn in
         * `onCreate` heraus und lässt das Fach leer zurück.
         */
        @Volatile
        private var uebergabe: ((Result<String?>) -> Unit)? = null

        /**
         * Läuft gerade eine Abfrage?
         *
         * Eigenes Merkmal und nicht „liegt noch etwas im Übergabefach": das Fach
         * ist leer, sobald die Activity den Rückkanal geholt hat — der Prompt
         * steht dann aber noch auf dem Bildschirm. Ohne diese Unterscheidung
         * startete eine zweite Anfrage mitten in die erste hinein und die
         * beendete Activity nähme den Rückkanal der zweiten mit.
         */
        private val laeuft = java.util.concurrent.atomic.AtomicBoolean(false)

        /**
         * Startet die Abfrage, falls keine läuft. Gibt `false` zurück, wenn
         * gerade schon eine offen ist.
         */
        fun starte(
            aufrufer: Activity,
            fach: String,
            modus: String,
            nachricht: String,
            geheimnis: String? = null,
            rueckkanal: (Result<String?>) -> Unit,
        ): Boolean {
            if (!laeuft.compareAndSet(false, true)) return false
            uebergabe = rueckkanal
            val absicht = Intent(aufrufer, SchluesselfachActivity::class.java).apply {
                putExtra(EXTRA_FACH, fach)
                putExtra(EXTRA_MODUS, modus)
                putExtra(EXTRA_NACHRICHT, nachricht)
                geheimnis?.let { putExtra(EXTRA_GEHEIMNIS, it) }
            }
            try {
                aufrufer.startActivity(absicht)
            } catch (e: Exception) {
                uebergabe = null
                laeuft.set(false)
                throw e
            }
            return true
        }

        /**
         * Kann dieses Gerät überhaupt biometrisch bestätigen?
         *
         * Die Bildschirmsperre zählt mit: wer keinen Finger angelernt hat, aber
         * eine PIN oder ein Muster benutzt, soll den Schnelleinstieg trotzdem
         * bekommen. Der Keystore bindet den Schlüssel an beides.
         */
        fun bestaetigungMoeglich(context: Context): Boolean =
            BiometricManager.from(context).canAuthenticate(erlaubteVerfahren()) ==
                BiometricManager.BIOMETRIC_SUCCESS

        /**
         * Unterhalb von Android 11 lässt sich die Bildschirmsperre nicht neben
         * der Biometrie zulassen — dort bleibt nur der Fingerabdruck.
         */
        fun erlaubteVerfahren(): Int =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                BiometricManager.Authenticators.BIOMETRIC_STRONG or
                    BiometricManager.Authenticators.DEVICE_CREDENTIAL
            } else {
                BiometricManager.Authenticators.BIOMETRIC_STRONG
            }
    }

    /** Der Rückkanal dieser Abfrage, aus dem Übergabefach geholt. */
    private var meinRueckkanal: ((Result<String?>) -> Unit)? = null

    private fun fertig(ergebnis: Result<String?>) {
        val rueckkanal = meinRueckkanal
        meinRueckkanal = null
        if (rueckkanal != null) {
            rueckkanal(ergebnis)
            laeuft.set(false)
        }
        finish()
    }

    /**
     * Die Notbremse. Verschwindet die Activity, ohne dass die Abfrage ein
     * Ergebnis hatte, wartet drüben ein blockierter Aufruf. Ohne diese Zeilen
     * wartete er bis zum Ende der Anwendung.
     */
    override fun onDestroy() {
        val rueckkanal = meinRueckkanal
        meinRueckkanal = null
        if (rueckkanal != null) {
            rueckkanal(Result.failure(SecurityException("Die Abfrage wurde beendet.")))
            laeuft.set(false)
        }
        super.onDestroy()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        meinRueckkanal = uebergabe
        uebergabe = null

        val fach = intent.getStringExtra(EXTRA_FACH)
        val modus = intent.getStringExtra(EXTRA_MODUS)
        val nachricht = intent.getStringExtra(EXTRA_NACHRICHT) ?: "Bestätigen"
        val geheimnis = intent.getStringExtra(EXTRA_GEHEIMNIS)

        if (fach == null || modus == null) {
            fertig(Result.failure(IllegalArgumentException("Unvollständiger Auftrag")))
            return
        }

        val cipher = try {
            when (modus) {
                MODUS_ABLEGEN -> Schluesselfach.cipherZumAblegen(fach)
                MODUS_LESEN -> Schluesselfach.cipherZumLesen(this, fach)
                else -> null
            }
        } catch (e: KeyPermanentlyInvalidatedException) {
            // Ein neu angelernter Fingerabdruck hat den Schlüssel ungültig
            // gemacht — so soll es sein, sonst käme ein fremdes Gesicht an das
            // Verwahrte. Was hier liegt, geht damit aber nie wieder auf, und ein
            // Rest, der bei jedem Versuch scheitert, sieht aus wie ein Defekt.
            // Deshalb wird er weggeräumt. Verloren geht nichts: im Fach liegt
            // immer nur eine Kopie des PIN, nie das einzige Stück.
            Schluesselfach.loesche(this, fach)
            fertig(
                Result.failure(
                    SecurityException(
                        "Die Biometrie dieses Geräts hat sich geändert. " +
                            "Bitte den Schnelleinstieg neu einrichten.",
                    ),
                ),
            )
            return
        } catch (e: Exception) {
            fertig(Result.failure(e))
            return
        }

        if (modus == MODUS_LESEN && cipher == null) {
            // Nichts hinterlegt. Keine Abfrage, keine Fehlermeldung — die
            // Antwort ist schlicht „da ist nichts".
            fertig(Result.success(null))
            return
        }
        if (cipher == null) {
            fertig(Result.failure(IllegalStateException("Kein Cipher für Modus $modus")))
            return
        }

        val angaben = BiometricPrompt.PromptInfo.Builder()
            .setTitle(nachricht)
            .setAllowedAuthenticators(erlaubteVerfahren())
            .apply {
                // Beides zusammen weist Android zurück: entweder die
                // Bildschirmsperre als Verfahren oder ein eigener Abbrechen-Knopf.
                if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
                    setNegativeButtonText("Abbrechen")
                }
            }
            .build()

        val abfrage = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(ergebnis: BiometricPrompt.AuthenticationResult) {
                    val freigegeben = ergebnis.cryptoObject?.cipher
                    if (freigegeben == null) {
                        fertig(Result.failure(IllegalStateException("Kein freigegebener Schlüssel")))
                        return
                    }
                    try {
                        val wert = when (modus) {
                            MODUS_ABLEGEN -> {
                                Schluesselfach.legeAb(
                                    this@SchluesselfachActivity,
                                    fach,
                                    freigegeben,
                                    geheimnis ?: "",
                                )
                                null
                            }
                            else -> Schluesselfach.lies(this@SchluesselfachActivity, fach, freigegeben)
                        }
                        fertig(Result.success(wert))
                    } catch (e: Exception) {
                        fertig(Result.failure(e))
                    }
                }

                override fun onAuthenticationError(code: Int, meldung: CharSequence) {
                    fertig(Result.failure(SecurityException("Bestätigung abgebrochen: $meldung")))
                }

                // `onAuthenticationFailed` heißt „dieser Versuch war es nicht",
                // nicht „Schluss". Das System lässt weiter probieren und meldet
                // sich über `onAuthenticationError`, wenn es aufgibt.
            },
        )

        abfrage.authenticate(angaben, BiometricPrompt.CryptoObject(cipher))
    }
}
