//! Discord Rich Presence (RPC) Integration für das Maunting Smart System (MSS).
//!
//! Kommuniziert ausschließlich lokal über die Windows Named Pipe `\\.\pipe\discord-ipc-0`
//! direkt mit dem auf demselben PC laufenden Discord-Client.
//!
//! Sicherheits- & Datenschutz-Invarianten:
//! - Keine Netzwerkverbindungen an Discord-Server.
//! - Keine Passwörter, Tokens, Server-IPs oder Chat-Inhalte übertragen.
//! - Nur generischer Status ("Verwaltet Server" / "MSS Desktop Companion").
//! - Zero External Dependencies (nutzt ausschließlich Standard-Library I/O und serde_json).
//! - Sofortige Deaktivierung möglich über `konfig.json` (`discord_rpc_aktiv: false`).

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::json;

/// Standard Discord Application Client ID für Maunting Smart System.
/// Kann hier direkt im Code angepasst oder in konfig.json überschrieben werden.
pub const STANDARD_DISCORD_CLIENT_ID: &str = "1512525013155057735";

static DISCORD_LAEUFT: AtomicBool = AtomicBool::new(false);
static DISCORD_STATUS: Mutex<Option<DiscordStatus>> = Mutex::new(None);

#[derive(Clone)]
struct DiscordStatus {
    details: String,
    state: String,
}

/// Öffnet die lokale Discord IPC Named Pipe (discord-ipc-0 bis discord-ipc-9).
fn open_pipe() -> Option<File> {
    for i in 0..10 {
        let pipe_name = format!(r"\\.\pipe\discord-ipc-{}", i);
        if let Ok(file) = OpenOptions::new().read(true).write(true).open(&pipe_name) {
            return Some(file);
        }
    }
    None
}

/// Sendet ein Paket mit 8-Byte Header (Opcode + Länge in Little-Endian) gefolgt von UTF-8 JSON.
fn send_packet(file: &mut File, opcode: u32, payload: &str) -> bool {
    let len = payload.len() as u32;
    let mut header = [0u8; 8];
    header[0..4].copy_from_slice(&opcode.to_le_bytes());
    header[4..8].copy_from_slice(&len.to_le_bytes());

    if file.write_all(&header).is_err() {
        return false;
    }
    if file.write_all(payload.as_bytes()).is_err() {
        return false;
    }
    file.flush().is_ok()
}

/// Liest ein Antwort-Paket von der Named Pipe.
fn read_packet(file: &mut File) -> Option<(u32, String)> {
    let mut header = [0u8; 8];
    if file.read_exact(&mut header).is_err() {
        return None;
    }
    let opcode = u32::from_le_bytes(header[0..4].try_into().unwrap());
    let length = u32::from_le_bytes(header[4..8].try_into().unwrap()) as usize;
    if length > 65536 {
        return None;
    }
    let mut buf = vec![0u8; length];
    if file.read_exact(&mut buf).is_err() {
        return None;
    }
    String::from_utf8(buf).ok().map(|s| (opcode, s))
}

/// Setzt die Rich Presence Aktivität.
fn send_activity(
    file: &mut File,
    details: &str,
    state: &str,
    start_timestamp: u64,
) -> bool {
    let pid = std::process::id();
    let nonce = uuid::Uuid::new_v4().to_string();
    let payload = json!({
        "cmd": "SET_ACTIVITY",
        "args": {
            "pid": pid,
            "activity": {
                "details": details,
                "state": state,
                "timestamps": {
                    "start": start_timestamp
                },
                "assets": {
                    "large_image": "logo",
                    "large_text": "Maunting Smart System"
                }
            }
        },
        "nonce": nonce
    })
    .to_string();

    send_packet(file, 1, &payload)
}

/// Startet den Hintergrund-Thread für Discord Rich Presence.
pub fn starten(client_id_override: Option<String>) {
    if DISCORD_LAEUFT.swap(true, Ordering::SeqCst) {
        return; // Läuft bereits
    }

    let client_id = client_id_override
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| STANDARD_DISCORD_CLIENT_ID.to_string());

    let start_timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();

    thread::Builder::new()
        .name("mss-discord-rpc".into())
        .spawn(move || {
            let mut pipe: Option<File> = None;

            while DISCORD_LAEUFT.load(Ordering::SeqCst) {
                if pipe.is_none() {
                    if let Some(mut p) = open_pipe() {
                        // Handshake: OpCode 0, v=1, client_id
                        let handshake = json!({
                            "v": 1,
                            "client_id": client_id
                        })
                        .to_string();

                        if send_packet(&mut p, 0, &handshake) {
                            if let Some((op, _resp)) = read_packet(&mut p) {
                                if op == 1 {
                                    pipe = Some(p);
                                }
                            }
                        }
                    }
                }

                if let Some(ref mut p) = pipe {
                    let current_status = {
                        let lock = DISCORD_STATUS.lock().unwrap();
                        lock.clone()
                    };

                    let (details, state) = match current_status {
                        Some(s) => (s.details, s.state),
                        None => (
                            "Maunting Smart System".to_string(),
                            "Server Manager Companion".to_string(),
                        ),
                    };

                    if !send_activity(p, &details, &state, start_timestamp) {
                        pipe = None; // Verbindung verloren, erneuter Verbindungsversuch
                    }
                }

                // Polling-Intervall für Status-Aktualisierung / Reconnect
                for _ in 0..15 {
                    if !DISCORD_LAEUFT.load(Ordering::SeqCst) {
                        break;
                    }
                    thread::sleep(Duration::from_secs(1));
                }
            }

            // Beenden: Aktivität leeren und Pipe schließen
            if let Some(ref mut p) = pipe {
                let pid = std::process::id();
                let clear_payload = json!({
                    "cmd": "SET_ACTIVITY",
                    "args": {
                        "pid": pid,
                        "activity": null
                    },
                    "nonce": uuid::Uuid::new_v4().to_string()
                })
                .to_string();
                let _ = send_packet(p, 1, &clear_payload);
                let _ = send_packet(p, 2, "{}");
            }
        })
        .ok();
}

/// Aktualisiert den angezeigten Status im Discord-Profil.
pub fn status_aktualisieren(details: &str, state: &str) {
    let mut lock = DISCORD_STATUS.lock().unwrap();
    *lock = Some(DiscordStatus {
        details: details.to_string(),
        state: state.to_string(),
    });
}

/// Beendet die Discord RPC Verbindung sauber.
pub fn beenden() {
    DISCORD_LAEUFT.store(false, Ordering::SeqCst);
}
