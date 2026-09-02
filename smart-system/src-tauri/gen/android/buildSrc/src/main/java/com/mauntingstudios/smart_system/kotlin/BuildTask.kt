import java.io.File
import org.gradle.api.DefaultTask
import org.gradle.api.GradleException
import org.gradle.api.tasks.Input
import org.gradle.api.tasks.TaskAction

open class BuildTask : DefaultTask() {
    @Input
    var rootDirRel: String? = null
    @Input
    var target: String? = null
    @Input
    var release: Boolean? = null

    @TaskAction
    fun assemble() {
        val target = target ?: throw GradleException("target cannot be null")
        val isRelease = release ?: false

        val triple = when (target) {
            "aarch64" -> "aarch64-linux-android"
            "arm" -> "armv7-linux-androideabi"
            "i686", "x86" -> "i686-linux-android"
            "x86_64" -> "x86_64-linux-android"
            else -> target
        }

        val abi = when (target) {
            "aarch64" -> "arm64-v8a"
            "arm" -> "armeabi-v7a"
            "i686", "x86" -> "x86"
            "x86_64" -> "x86_64"
            else -> target
        }

        val srcTauriDir = File(project.projectDir, "../../..").canonicalFile
        val variantDir = if (isRelease) "release" else "debug"
        val builtSo = File(srcTauriDir, "target/$triple/$variantDir/libmaunting_smart_system_lib.so")

        val jniDir = File(project.projectDir, "src/main/jniLibs/$abi")
        jniDir.mkdirs()
        val targetSo = File(jniDir, "libmaunting_smart_system_lib.so")

        if (builtSo.exists()) {
            project.logger.lifecycle("Found existing built library at ${builtSo.absolutePath}, copying to JNI...")
            if (targetSo.exists()) {
                targetSo.delete()
            }
            builtSo.copyTo(targetSo, overwrite = true)
            return
        }

        val userHome = System.getProperty("user.home") ?: ""
        val cargoExeCandidate = File(userHome, ".cargo/bin/cargo.exe")
        val cargoCmd = if (cargoExeCandidate.exists()) cargoExeCandidate.absolutePath else "cargo"

        val ndk = System.getenv("NDK_HOME") ?: System.getenv("ANDROID_NDK_HOME") ?: "${System.getenv("LOCALAPPDATA")}\\Android\\Sdk\\ndk\\27.2.12479018"
        val llvmBin = "$ndk\\toolchains\\llvm\\prebuilt\\windows-x86_64\\bin"
        val currentPath = System.getenv("PATH") ?: ""
        val extraCargoBin = if (userHome.isNotEmpty()) "$userHome\\.cargo\\bin;" else ""

        val cargoArgs = mutableListOf(
            "build",
            "--package", "maunting-smart-system",
            "--manifest-path", File(srcTauriDir, "Cargo.toml").absolutePath,
            "--target", triple,
            "--lib"
        )
        if (isRelease) {
            cargoArgs.add("--release")
        }

        project.exec {
            environment("NDK_HOME", ndk)
            environment("PATH", "$llvmBin;$extraCargoBin$currentPath")
            environment("CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER", "$llvmBin\\aarch64-linux-android26-clang.cmd")
            environment("CARGO_TARGET_ARMV7_LINUX_ANDROIDEABI_LINKER", "$llvmBin\\armv7a-linux-androideabi26-clang.cmd")
            environment("CARGO_TARGET_I686_LINUX_ANDROID_LINKER", "$llvmBin\\i686-linux-android26-clang.cmd")
            environment("CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER", "$llvmBin\\x86_64-linux-android26-clang.cmd")
            workingDir(srcTauriDir)
            executable(cargoCmd)
            args(cargoArgs)
        }.assertNormalExitValue()

        if (builtSo.exists()) {
            if (targetSo.exists()) {
                targetSo.delete()
            }
            builtSo.copyTo(targetSo, overwrite = true)
        }
    }
}