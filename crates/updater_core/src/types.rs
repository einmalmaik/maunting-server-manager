use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct UpdateInfo {
    pub verfuegbar: bool,
    pub aktuelle_version: String,
    pub neue_version: Option<String>,
    pub download_url: Option<String>,
    pub notizen: Option<String>,
    pub ist_android: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct UpdateStatusEvent {
    pub status: String,
    pub prozent: Option<u32>,
    pub fehler: Option<String>,
}
