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
            "aarch64", "arm64" -> "aarch64-linux-android"
            "arm", "armv7" -> "armv7-linux-androideabi"
            "i686", "x86" -> "i686-linux-android"
            "x86_64" -> "x86_64-linux-android"
            else -> target
        }

        val abi = when (target) {
            "aarch64", "arm64" -> "arm64-v8a"
            "arm", "armv7" -> "armeabi-v7a"
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
        val isWindows = System.getProperty("os.name")?.lowercase()?.contains("win") == true
        val cargoExeCandidate = File(userHome, ".cargo/bin/cargo" + if (isWindows) ".exe" else "")
        val cargoCmd = if (cargoExeCandidate.exists()) cargoExeCandidate.absolutePath else "cargo"

        val ndkEnv = System.getenv("NDK_HOME") ?: System.getenv("ANDROID_NDK_HOME")
        val ndkDir = if (!ndkEnv.isNullOrEmpty()) {
            File(ndkEnv)
        } else {
            val localApp = System.getenv("LOCALAPPDATA") ?: ""
            File(localApp, "Android/Sdk/ndk/27.2.12479018")
        }

        val prebuiltDir = File(ndkDir, "toolchains/llvm/prebuilt")
        val prebuiltHost = prebuiltDir.listFiles()?.firstOrNull { it.isDirectory }
        val llvmBin = if (prebuiltHost != null) File(prebuiltHost, "bin") else File(prebuiltDir, if (isWindows) "windows-x86_64/bin" else "linux-x86_64/bin")
        val llvmBinPath = llvmBin.absolutePath

        val currentPath = System.getenv("PATH") ?: ""
        val pathSep = File.pathSeparator
        val extraCargoBin = if (userHome.isNotEmpty()) "${File(userHome, ".cargo/bin").absolutePath}$pathSep" else ""

        val clangExt = if (isWindows) ".cmd" else ""
        fun findClang(prefix: String, targetApiPrefix: String): String {
            val candidate = File(llvmBin, "$prefix$clangExt")
            if (candidate.exists()) return candidate.absolutePath
            val matched = llvmBin.listFiles()?.firstOrNull { it.name.startsWith(targetApiPrefix) && it.name.contains("clang") && it.name.endsWith(clangExt) && !it.name.contains("++") }
            return matched?.absolutePath ?: candidate.absolutePath
        }

        val clangAarch64 = findClang("aarch64-linux-android24-clang", "aarch64-linux-android")
        val clangArmv7 = findClang("armv7a-linux-androideabi24-clang", "armv7a-linux-androideabi")
        val clangI686 = findClang("i686-linux-android24-clang", "i686-linux-android")
        val clangX86_64 = findClang("x86_64-linux-android24-clang", "x86_64-linux-android")

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
            environment("NDK_HOME", ndkDir.absolutePath)
            environment("PATH", "$llvmBinPath$pathSep$extraCargoBin$currentPath")
            environment("CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER", clangAarch64)
            environment("CARGO_TARGET_ARMV7_LINUX_ANDROIDEABI_LINKER", clangArmv7)
            environment("CARGO_TARGET_I686_LINUX_ANDROID_LINKER", clangI686)
            environment("CARGO_TARGET_X86_64_LINUX_ANDROID_LINKER", clangX86_64)
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