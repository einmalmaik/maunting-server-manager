use std::path::PathBuf;

fn find_ndk() -> Option<PathBuf> {
    if let Ok(ndk) = std::env::var("NDK_HOME").or_else(|_| std::env::var("ANDROID_NDK_HOME")) {
        let p = PathBuf::from(ndk);
        if p.exists() {
            return Some(p);
        }
    }
    let sdk_roots = [
        std::env::var("ANDROID_HOME").ok(),
        std::env::var("ANDROID_SDK_ROOT").ok(),
        std::env::var("LOCALAPPDATA").ok().map(|l| format!(r"{l}\Android\Sdk")),
        Some("/usr/local/lib/android/sdk".to_string()),
        Some("/opt/android-sdk".to_string()),
    ];
    for root in sdk_roots.into_iter().flatten() {
        let ndk_dir = PathBuf::from(root).join("ndk");
        if let Ok(entries) = std::fs::read_dir(&ndk_dir) {
            let mut versions: Vec<PathBuf> = entries
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| p.is_dir())
                .collect();
            versions.sort();
            if let Some(highest) = versions.pop() {
                return Some(highest);
            }
        }
    }
    None
}

fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("android") {
        if let Some(ndk) = find_ndk() {
            let target_arch = std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
            let arch_triple = match target_arch.as_str() {
                "aarch64" => "aarch64-linux-android",
                "arm" => "arm-linux-androideabi",
                "x86" => "i686-linux-android",
                "x86_64" => "x86_64-linux-android",
                _ => "aarch64-linux-android",
            };

            let prebuilt = ndk.join("toolchains").join("llvm").join("prebuilt");
            let host_tag = if prebuilt.join("linux-x86_64").exists() {
                "linux-x86_64"
            } else if prebuilt.join("windows-x86_64").exists() {
                "windows-x86_64"
            } else if prebuilt.join("darwin-x86_64").exists() {
                "darwin-x86_64"
            } else if prebuilt.join("darwin-aarch64").exists() {
                "darwin-aarch64"
            } else {
                "linux-x86_64"
            };

            let sysroot_lib = prebuilt
                .join(host_tag)
                .join("sysroot")
                .join("usr")
                .join("lib")
                .join(arch_triple);

            for api in ["26", "27", "28", "29", "30", "31", "32", "33", "34"] {
                let candidate = sysroot_lib.join(api);
                if candidate.exists() {
                    println!("cargo:rustc-link-search=native={}", candidate.display());
                    break;
                }
            }
        }
        println!("cargo:rustc-link-arg=-Wl,-z,max-page-size=16384");
    }
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        println!("cargo:rustc-link-arg=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'");
    }
    tauri_build::build()
}
