//! Die Tauri-Befehle zu Token und Fächern.
//!
//! Die Namen sind die, die die Oberfläche kennt (`desktop/tauri.ts`,
//! `desktop/transport.ts`). Eine App hängt sie in ihr `generate_handler!` ein,
//! etwa `schluesselfach::befehle::refresh_token_laden`.

use tauri::AppHandle;

use crate::{biometrie, geheimnisse};

/// Refresh-Token in den OS-Tresor bzw. Sandbox-Speicher. Das Access-Token wird
/// bewusst nie gespeichert — es lebt nur im flüchtigen Speicher des Frontends.
#[tauri::command(async)]
pub fn refresh_token_speichern(app: AppHandle, token: String) -> Result<(), String> {
    geheimnisse::speichern(&app, &token)
}

#[tauri::command(async)]
pub fn refresh_token_laden(app: AppHandle) -> Result<Option<String>, String> {
    geheimnisse::laden(&app)
}

#[tauri::command(async)]
pub fn refresh_token_loeschen(app: AppHandle) -> Result<(), String> {
    geheimnisse::loeschen(&app)
}

/// Prüft, ob Windows Hello oder biometrische Authentifizierung verfügbar ist.
#[tauri::command(async)]
pub async fn biometrie_verfuegbar(app: AppHandle) -> bool {
    biometrie::pruefe_verfuegbarkeit(&app).await
}

/// Fordert den Benutzer zur biometrischen Bestätigung (Windows Hello) auf.
#[tauri::command(async)]
pub async fn biometrie_verifizieren(app: AppHandle, nachricht: Option<String>) -> Result<bool, String> {
    let msg = nachricht.unwrap_or_else(|| "Tresor entsperren".to_string());
    biometrie::verifiziere_benutzer(&app, &msg).await
}

/// Das angesprochene Schlüsselfach. Fehlt die Angabe, ist der Tresor gemeint —
/// er war vor der Trennung von Tresor und Messenger der einzige Nutzer.
fn fach_oder_tresor(fach: Option<String>) -> String {
    fach.unwrap_or_else(|| biometrie::FACH_TRESOR.to_string())
}

/// Speichert das biometrische Geheimnis im geschützten Schlüsselspeicher.
///
/// `async`, weil der Android-Keystore vor dem Ablegen selbst nach einer
/// Bestätigung fragt: auf dem Hauptthread stünde die Anwendung, solange der
/// Fingerabdruck-Dialog offen ist — und der Dialog käme gar nicht erst.
#[tauri::command(async)]
pub async fn biometrie_speichern(
    app: AppHandle,
    geheimnis: String,
    fach: Option<String>,
) -> Result<(), String> {
    biometrie::speichere_biometrie_geheimnis(&app, &fach_oder_tresor(fach), &geheimnis)
}

/// Fordert die Bestätigung an und gibt das Geheimnis erst danach heraus.
#[tauri::command(async)]
pub async fn biometrie_entsperren(
    app: AppHandle,
    nachricht: Option<String>,
    fach: Option<String>,
) -> Result<String, String> {
    let msg = nachricht.unwrap_or_else(|| "Tresor entsperren".to_string());
    biometrie::entsperre_mit_biometrie(&app, &fach_oder_tresor(fach), &msg).await
}

/// Entfernt das biometrische Geheimnis aus dem Schlüsselspeicher.
#[tauri::command(async)]
pub async fn biometrie_loeschen(app: AppHandle, fach: Option<String>) -> Result<(), String> {
    biometrie::loesche_biometrie_geheimnis(&app, &fach_oder_tresor(fach))
}

/// Meldet, ob diese Plattform ein Geheimnis verwahren kann. Nicht dasselbe wie
/// `biometrie_verfuegbar`: fragen können und verwahren können sind zwei Dinge.
#[tauri::command]
pub fn biometrie_speicher_verfuegbar(app: AppHandle) -> bool {
    biometrie::geheimnisspeicher_verfuegbar(&app)
}

/// Fragt der Schlüsselspeicher beim Ablegen von sich aus nach einer Bestätigung?
/// Android ja, Windows nein.
#[tauri::command]
pub fn biometrie_speicher_fragt_selbst() -> bool {
    biometrie::speicher_fragt_selbst()
}

/// Das Gerätegeheimnis des Messengers, falls eines hinterlegt ist (ohne
/// Abfrage, siehe `biometrie::lies_geraetegeheimnis`).
#[tauri::command(async)]
pub async fn messenger_geraetegeheimnis(app: AppHandle) -> Result<Option<String>, String> {
    biometrie::lies_geraetegeheimnis(&app)
}
