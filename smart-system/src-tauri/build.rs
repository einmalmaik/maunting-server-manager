fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("android") {
        let ndk = std::env::var("NDK_HOME")
            .or_else(|_| std::env::var("ANDROID_NDK_HOME"))
            .unwrap_or_else(|_| {
                let local_appdata = std::env::var("LOCALAPPDATA").unwrap_or_default();
                format!(r"{local_appdata}\Android\Sdk\ndk\27.2.12479018")
            });
        let target_arch = std::env::var("CARGO_CFG_TARGET_ARCH").unwrap_or_default();
        let arch_triple = match target_arch.as_str() {
            "aarch64" => "aarch64-linux-android",
            "arm" => "arm-linux-androideabi",
            "x86" => "i686-linux-android",
            "x86_64" => "x86_64-linux-android",
            _ => "aarch64-linux-android",
        };
        let lib_dir = format!(
            r"{ndk}\toolchains\llvm\prebuilt\windows-x86_64\sysroot\usr\lib\{arch_triple}\26"
        );
        println!("cargo:rustc-link-search=native={lib_dir}");
        println!("cargo:rustc-link-arg=-Wl,-z,max-page-size=16384");
    }
    tauri_build::build()
}
