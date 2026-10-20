//! App-spezifische Update-Konfiguration.

#[derive(Clone, Debug)]
pub struct UpdateConfig {
    /// Lesbarer Name der App (z. B. "Pontis" oder "MSS")
    pub app_name: &'static str,
    /// Android Package-ID (z. B. "com.mauntingstudios.secure_browser" oder "com.mauntingstudios.smart_system")
    pub android_package: &'static str,
    /// Statisches Release-Asset JSON (z. B. "latest-msb.json" oder "latest.json")
    pub asset_json_name: &'static str,
    /// Name der APK-Datei im Release (z. B. "MauntingSecureBrowser.apk" oder "MauntingSmartSystem.apk")
    pub apk_file_name: &'static str,
    /// Fallback-GitHub-API URL (strikte Vorgabe: https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest)
    pub fallback_api_url: &'static str,
    /// Basis-URL für statische Assets (z. B. https://github.com/einmalmaik/maunting-server-manager/releases/latest/download)
    pub release_download_base: &'static str,
    /// Velopack channel feed hosted as a signed asset in GitHub Releases.
    pub velopack_feed_url: &'static str,
    /// GitHub repository containing signed Velopack feed and package assets.
    pub velopack_repo_url: &'static str,
    /// Velopack channel for this app ("pontis" or "mss").
    pub velopack_channel: &'static str,
    /// User-Agent für HTTP-Requests
    pub user_agent: &'static str,
}

impl UpdateConfig {
    pub fn pontis() -> Self {
        Self {
            app_name: "Pontis",
            android_package: "com.mauntingstudios.secure_browser",
            asset_json_name: "latest-msb.json",
            apk_file_name: "MauntingSecureBrowser.apk",
            fallback_api_url:
                "https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest",
            release_download_base:
                "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download",
            velopack_feed_url:
                "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download",
            velopack_repo_url: "https://github.com/einmalmaik/maunting-server-manager",
            velopack_channel: "pontis",
            user_agent: "MauntingSecureBrowser",
        }
    }

    pub fn mss() -> Self {
        Self {
            app_name: "Maunting Smart System",
            android_package: "com.mauntingstudios.smart_system",
            asset_json_name: "latest.json",
            apk_file_name: "MauntingSmartSystem.apk",
            fallback_api_url:
                "https://api.github.com/repos/einmalmaik/maunting-server-manager/releases/latest",
            release_download_base:
                "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download",
            velopack_feed_url:
                "https://github.com/einmalmaik/maunting-server-manager/releases/latest/download",
            velopack_repo_url: "https://github.com/einmalmaik/maunting-server-manager",
            velopack_channel: "mss",
            user_agent: "MauntingSmartSystem",
        }
    }
}
