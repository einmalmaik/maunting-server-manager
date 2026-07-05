// MSM Backup Recovery - Tauri v2 backend commands.
//
// Implements tar.gz extraction, file tree building, save (copy), and
// text file preview for the M2 "Full Features" milestone.
//
// All commands return `Result<T, String>` so the frontend receives clear
// error strings (German-friendly) instead of opaque Rust debug output.

use std::fs;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use flate2::read::GzDecoder;
use tar::Archive;
use tauri::command;
use zip::CompressionMethod::Stored;
use zip::ZipWriter;
use zip::write::SimpleFileOptions;

// ---------------------------------------------------------------------------
// Temp-file tracking (VAL-CROSS-004: cleanup on app close)
// ---------------------------------------------------------------------------

/// Global list of temp directories created by the frontend during a recovery
/// session. Tracked so the `RunEvent::Exit` handler in `lib.rs` can delete
/// every leftover temp dir even if the frontend didn't clean up explicitly.
static TEMP_DIRS: Mutex<Vec<String>> = Mutex::new(Vec::new());

/// Register a temp dir path for cleanup-on-exit.
pub fn track_temp_dir(path: &str) {
    if let Ok(mut dirs) = TEMP_DIRS.lock() {
        if !dirs.iter().any(|p| p == path) {
            dirs.push(path.to_string());
        }
    }
}

/// Remove a path from tracking (after explicit cleanup by the frontend).
fn untrack_temp_dir(path: &str) {
    if let Ok(mut dirs) = TEMP_DIRS.lock() {
        dirs.retain(|p| p != path);
    }
}

/// Delete every tracked temp directory. Called from the `RunEvent::Exit`
/// handler in `lib.rs` so no decrypted artifacts remain on disk after the
/// app closes (VAL-CROSS-004).
pub fn cleanup_all_temp_dirs() {
    let dirs: Vec<String> = TEMP_DIRS.lock().map(|d| d.clone()).unwrap_or_default();
    for dir in dirs {
        let _ = fs::remove_dir_all(&dir);
    }
    if let Ok(mut d) = TEMP_DIRS.lock() {
        d.clear();
    }
}

/// Recursive file-tree node serialized to the frontend.
///
/// `name`  - basename of the entry (e.g. "manifest.json")
/// `path`  - absolute path of the entry on disk
/// `is_dir`- true for directories, false for files
/// `size`  - file size in bytes (0 for directories)
/// `children` - sorted child nodes (only populated for directories)
#[derive(serde::Serialize, Debug, Clone, PartialEq, Eq)]
pub struct FileTree {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
    pub size: u64,
    pub children: Vec<FileTree>,
}

/// Build a recursive `FileTree` from a directory on disk.
///
/// Entries are sorted alphabetically (directories first, then files) to
/// give the frontend a stable, predictable ordering. Symlinks and other
/// non-file/non-dir entry types are skipped to keep the tree simple (KISS).
pub fn build_file_tree<P: AsRef<Path>>(root: P) -> Result<FileTree, String> {
    let root = root.as_ref();
    let meta = fs::metadata(root).map_err(|e| format!("Pfad nicht lesbar: {}", e))?;
    if !meta.is_dir() {
        return Err(format!(
            "Kein Verzeichnis: {}",
            root.display()
        ));
    }

    let name = root
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| root.display().to_string());

    Ok(FileTree {
        name,
        path: root.display().to_string(),
        is_dir: true,
        size: 0,
        children: collect_children(root)?,
    })
}

/// Recursively collect sorted children of a directory.
fn collect_children(dir: &Path) -> Result<Vec<FileTree>, String> {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|e| format!("Verzeichnis nicht lesbar: {}", e))?
        .filter_map(|res| res.ok().map(|e| e.path()))
        .collect();

    // Sort: directories first, then by name. Stable & deterministic.
    entries.sort_by(|a, b| {
        let a_is_dir = fs::metadata(a).map(|m| m.is_dir()).unwrap_or(false);
        let b_is_dir = fs::metadata(b).map(|m| m.is_dir()).unwrap_or(false);
        match (a_is_dir, b_is_dir) {
            (true, false) => std::cmp::Ordering::Less,
            (false, true) => std::cmp::Ordering::Greater,
            _ => a.file_name().cmp(&b.file_name()),
        }
    });

    let mut children = Vec::with_capacity(entries.len());
    for path in entries {
        let meta = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(_) => continue, // skip unreadable entries
        };

        // Skip symlinks to avoid cycles (KISS, security).
        if meta.file_type().is_symlink() {
            continue;
        }

        let name = path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default();

        if meta.is_dir() {
            children.push(FileTree {
                name,
                path: path.display().to_string(),
                is_dir: true,
                size: 0,
                children: collect_children(&path)?,
            });
        } else if meta.is_file() {
            children.push(FileTree {
                name,
                path: path.display().to_string(),
                is_dir: false,
                size: meta.len(),
                children: Vec::new(),
            });
        }
        // Other types (sockets, devices, etc.) are skipped.
    }
    Ok(children)
}

/// Recursively copy a directory's contents into a target directory.
///
/// `source_dir` is copied *into* `target_dir` (target gets the contents,
/// not the source folder itself as a wrapper). The target directory is
/// created if it does not exist.
pub fn copy_dir_contents<P: AsRef<Path>, Q: AsRef<Path>>(
    source_dir: P,
    target_dir: Q,
) -> Result<(), String> {
    let source = source_dir.as_ref();
    let target = target_dir.as_ref();

    let src_meta = fs::metadata(source).map_err(|e| format!("Quelle nicht lesbar: {}", e))?;
    if !src_meta.is_dir() {
        return Err(format!("Quelle ist kein Verzeichnis: {}", source.display()));
    }

    fs::create_dir_all(target).map_err(|e| format!("Zielverzeichnis nicht erstellbar: {}", e))?;

    for entry in fs::read_dir(source).map_err(|e| format!("Quelle nicht lesbar: {}", e))? {
        let entry = entry.map_err(|e| format!("Eintrag nicht lesbar: {}", e))?;
        let path = entry.path();
        let file_name = entry.file_name();
        let dest = target.join(&file_name);

        let meta = match fs::symlink_metadata(&path) {
            Ok(m) => m,
            Err(e) => return Err(format!("Eintrag nicht lesbar: {}", e)),
        };

        if meta.file_type().is_symlink() {
            // Skip symlinks (security + KISS).
            continue;
        }

        if meta.is_dir() {
            copy_dir_contents(&path, &dest)?;
        } else if meta.is_file() {
            copy_one_file(&path, &dest)?;
        }
    }
    Ok(())
}

/// Copy a single file, streaming bytes to avoid loading large files
/// fully into memory.
fn copy_one_file(src: &Path, dest: &Path) -> Result<(), String> {
    let mut reader =
        fs::File::open(src).map_err(|e| format!("Datei nicht lesbar: {}", e))?;
    let mut writer =
        fs::File::create(dest).map_err(|e| format!("Datei nicht schreibbar: {}", e))?;
    io::copy(&mut reader, &mut writer)
        .map_err(|e| format!("Kopieren fehlgeschlagen: {}", e))?;
    writer
        .flush()
        .map_err(|e| format!("Schreiben fehlgeschlagen: {}", e))?;
    Ok(())
}

/// Extract a `.tar.gz` archive into `output_dir` and return the file tree
/// of the extracted contents.
///
/// Errors are surfaced as clear strings so the frontend can display them
/// directly (e.g. corrupt gzip header, invalid tar stream, missing file).
#[command]
pub fn extract_tar_gz(tar_gz_path: String, output_dir: String) -> Result<FileTree, String> {
    let tar_gz_path = PathBuf::from(&tar_gz_path);
    let output_dir = PathBuf::from(&output_dir);

    if !tar_gz_path.exists() {
        return Err(format!(
            "tar.gz-Datei nicht gefunden: {}",
            tar_gz_path.display()
        ));
    }

    // Open the gzip stream. A non-gzip file fails here with a clear error.
    let tar_gz = fs::File::open(&tar_gz_path)
        .map_err(|e| format!("tar.gz-Datei nicht lesbar: {}", e))?;
    let gz = GzDecoder::new(tar_gz);

    // Create the output directory so unpack can write into it.
    fs::create_dir_all(&output_dir)
        .map_err(|e| format!("Zielverzeichnis nicht erstellbar: {}", e))?;

    let mut archive = Archive::new(gz);
    archive
        .unpack(&output_dir)
        .map_err(|e| format!("Entpacken fehlgeschlagen: {}", e))?;

    build_file_tree(&output_dir)
}

/// Copy all extracted files from `source_dir` to a user-chosen `target_dir`.
#[command]
pub fn save_extracted(source_dir: String, target_dir: String) -> Result<(), String> {
    copy_dir_contents(&source_dir, &target_dir)
}

/// Save all extracted files from `source_dir` as a ZIP archive at `zip_path`.
///
/// Uses no compression (`Stored`) because the source files are already from a
/// backup archive — recompressing would waste CPU without meaningful size
/// reduction. Symlinks are skipped for security (same as `copy_dir_contents`).
#[command]
pub fn save_as_zip(source_dir: String, zip_path: String) -> Result<(), String> {
    let source = Path::new(&source_dir);
    if !source.is_dir() {
        return Err("Quellverzeichnis existiert nicht oder ist kein Verzeichnis.".into());
    }

    let file = fs::File::create(&zip_path)
        .map_err(|e| format!("ZIP-Datei konnte nicht erstellt werden: {e}"))?;
    let mut zip = ZipWriter::new(file);
    let options = SimpleFileOptions::default().compression_method(Stored);

    let mut buffer = Vec::new();
    add_dir_to_zip(&mut zip, source, source, options, &mut buffer)?;

    zip.finish()
        .map_err(|e| format!("ZIP-Archiv konnte nicht abgeschlossen werden: {e}"))?;
    Ok(())
}

/// Recursively add all files under `current` to the ZIP, using paths relative
/// to `base` as archive entry names. Symlinks are skipped (security).
fn add_dir_to_zip(
    zip: &mut ZipWriter<fs::File>,
    base: &Path,
    current: &Path,
    options: SimpleFileOptions,
    buffer: &mut Vec<u8>,
) -> Result<(), String> {
    let entries = fs::read_dir(current)
        .map_err(|e| format!("Verzeichnis konnte nicht gelesen werden: {e}"))?;

    for entry in entries {
        let entry = entry.map_err(|e| format!("Verzeichniseintrag konnte nicht gelesen werden: {e}"))?;
        let path = entry.path();
        let name = path.strip_prefix(base)
            .map_err(|e| format!("Pfad konnte nicht relativiert werden: {e}"))?
            .to_string_lossy()
            .replace('\\', "/");

        // Skip symlinks (security)
        if path.is_symlink() {
            continue;
        }

        if path.is_dir() {
            add_dir_to_zip(zip, base, &path, options, buffer)?;
        } else if path.is_file() {
            zip.start_file(&name, options)
                .map_err(|e| format!("Datei konnte nicht zum ZIP hinzugefügt werden: {e}"))?;
            let mut f = fs::File::open(&path)
                .map_err(|e| format!("Datei konnte nicht geöffnet werden: {e}"))?;
            f.read_to_end(buffer)
                .map_err(|e| format!("Datei konnte nicht gelesen werden: {e}"))?;
            zip.write_all(buffer)
                .map_err(|e| format!("Datei konnte nicht in ZIP geschrieben werden: {e}"))?;
            buffer.clear();
        }
    }
    Ok(())
}

/// Read a text file's content (UTF-8) for preview in the frontend.
#[command]
pub fn read_text_file(path: String) -> Result<String, String> {
    let path = PathBuf::from(&path);
    if !path.exists() {
        return Err(format!("Datei nicht gefunden: {}", path.display()));
    }
    let meta = fs::metadata(&path).map_err(|e| format!("Datei nicht lesbar: {}", e))?;
    if !meta.is_file() {
        return Err(format!("Keine Datei: {}", path.display()));
    }
    // Guard against accidentally reading huge files into the preview.
    const MAX_PREVIEW_BYTES: u64 = 2 * 1024 * 1024; // 2 MiB
    if meta.len() > MAX_PREVIEW_BYTES {
        return Err(format!(
            "Datei zu gross fuer Vorschau (max {} Bytes): {}",
            MAX_PREVIEW_BYTES,
            meta.len()
        ));
    }
    fs::read_to_string(&path).map_err(|e| format!("Datei nicht lesbar: {}", e))
}

// ---------------------------------------------------------------------------
// Temp-file management commands (M2: recovery-full-ui)
// ---------------------------------------------------------------------------

/// Create a unique temp directory for a recovery session and register it for
/// cleanup on app exit (VAL-CROSS-004). Returns the absolute path.
#[command]
pub fn create_temp_dir() -> Result<String, String> {
    let mut buf = [0u8; 16];
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    buf.copy_from_slice(&nanos.to_le_bytes());
    let suffix: String = buf.iter().map(|b| format!("{:02x}", b)).collect();

    let dir = std::env::temp_dir().join(format!("msm-recovery-{}", suffix));
    fs::create_dir_all(&dir).map_err(|e| format!("Temp-Verzeichnis nicht erstellbar: {}", e))?;

    let path = dir.display().to_string();
    track_temp_dir(&path);
    Ok(path)
}

/// Write raw bytes to a file inside a temp directory. Returns the full path
/// of the written file. Used to persist the decrypted tar.gz before calling
/// `extract_tar_gz`.
#[command]
pub fn write_temp_file(dir_path: String, filename: String, data: Vec<u8>) -> Result<String, String> {
    let dir = PathBuf::from(&dir_path);
    if !dir.is_dir() {
        return Err(format!("Verzeichnis existiert nicht: {}", dir.display()));
    }
    let file_path = dir.join(&filename);
    let mut file =
        fs::File::create(&file_path).map_err(|e| format!("Datei nicht erstellbar: {}", e))?;
    file.write_all(&data)
        .map_err(|e| format!("Schreiben fehlgeschlagen: {}", e))?;
    file.flush().map_err(|e| format!("Schreiben fehlgeschlagen: {}", e))?;
    Ok(file_path.display().to_string())
}

/// Recursively delete a temp directory and remove it from exit-tracking.
/// Called by the frontend when the user starts a new session (reset).
///
/// On Windows, `fs::remove_dir_all` can fail with "Access is denied" when a
/// file handle is briefly held by the OS, antivirus, or another process. To
/// keep cleanup robust on Windows CI, the removal is retried up to 3 times
/// with short 100ms delays between attempts.
#[command]
pub fn cleanup_temp_dir(dir_path: String) -> Result<(), String> {
    let path = PathBuf::from(&dir_path);
    if path.exists() {
        let mut last_err = None;
        for attempt in 0..3u32 {
            match fs::remove_dir_all(&path) {
                Ok(()) => {
                    untrack_temp_dir(&dir_path);
                    return Ok(());
                }
                Err(e) => {
                    last_err = Some(e);
                    if attempt < 2 {
                        std::thread::sleep(std::time::Duration::from_millis(100));
                    }
                }
            }
        }
        return Err(format!("Löschen fehlgeschlagen: {}", last_err.unwrap()));
    }
    untrack_temp_dir(&dir_path);
    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::io::Write;

    /// Build a tar.gz archive (in memory) containing a small nested tree:
    ///   root/
    ///     a.txt            -> "alpha\n"
    ///     sub/
    ///       b.txt          -> "beta\n"
    ///       manifest.json  -> "{}\n"
    fn make_test_tar_gz() -> Vec<u8> {
        let mut tar_builder = tar::Builder::new(Vec::new());

        // root/ directory
        let mut dir_header = tar::Header::new_gnu();
        dir_header.set_path("root").unwrap();
        dir_header.set_size(0);
        dir_header.set_mode(0o755);
        dir_header.set_entry_type(tar::EntryType::Directory);
        dir_header.set_cksum();
        tar_builder.append(&dir_header, std::io::empty()).unwrap();

        // root/a.txt
        let a_content = b"alpha\n";
        let mut file_header = tar::Header::new_gnu();
        file_header.set_path("root/a.txt").unwrap();
        file_header.set_size(a_content.len() as u64);
        file_header.set_mode(0o644);
        file_header.set_entry_type(tar::EntryType::Regular);
        file_header.set_cksum();
        tar_builder.append(&file_header, Cursor::new(a_content)).unwrap();

        // root/sub/ directory
        let mut sub_header = tar::Header::new_gnu();
        sub_header.set_path("root/sub").unwrap();
        sub_header.set_size(0);
        sub_header.set_mode(0o755);
        sub_header.set_entry_type(tar::EntryType::Directory);
        sub_header.set_cksum();
        tar_builder.append(&sub_header, std::io::empty()).unwrap();

        // root/sub/b.txt
        let b_content = b"beta\n";
        let mut b_header = tar::Header::new_gnu();
        b_header.set_path("root/sub/b.txt").unwrap();
        b_header.set_size(b_content.len() as u64);
        b_header.set_mode(0o644);
        b_header.set_entry_type(tar::EntryType::Regular);
        b_header.set_cksum();
        tar_builder.append(&b_header, Cursor::new(b_content)).unwrap();

        // root/sub/manifest.json
        let m_content = b"{}\n";
        let mut m_header = tar::Header::new_gnu();
        m_header.set_path("root/sub/manifest.json").unwrap();
        m_header.set_size(m_content.len() as u64);
        m_header.set_mode(0o644);
        m_header.set_entry_type(tar::EntryType::Regular);
        m_header.set_cksum();
        tar_builder.append(&m_header, Cursor::new(m_content)).unwrap();

        let tar_bytes = tar_builder.into_inner().unwrap();

        // gzip the tar bytes
        let mut gz_buf = Vec::new();
        let mut encoder = GzEncoder::new(&mut gz_buf, Compression::default());
        encoder.write_all(&tar_bytes).unwrap();
        encoder.finish().unwrap();
        gz_buf
    }

    /// Write a tar.gz fixture to a temp file and return its path.
    fn write_tar_gz_fixture(dir: &Path, name: &str) -> PathBuf {
        let path = dir.join(name);
        let bytes = make_test_tar_gz();
        fs::write(&path, &bytes).unwrap();
        path
    }

    /// Recursively verify a FileTree node matches expected structure.
    fn find_child<'a>(node: &'a FileTree, name: &str) -> Option<&'a FileTree> {
        node.children.iter().find(|c| c.name == name)
    }

    #[test]
    fn test_extract_tar_gz_builds_correct_file_tree() {
        let tmp = tempfile_dir();
        let archive_path = write_tar_gz_fixture(&tmp, "test.tar.gz");
        let out_dir = tmp.join("out");
        let tree = extract_tar_gz(
            archive_path.to_string_lossy().into_owned(),
            out_dir.to_string_lossy().into_owned(),
        )
        .expect("extraction should succeed");

        // The root of the tree is the output dir itself.
        assert!(tree.is_dir, "root tree node should be a directory");

        // It should contain a "root" entry (the archive's top folder).
        let root = find_child(&tree, "root")
            .expect("expected 'root' entry from archive");
        assert!(root.is_dir);

        // root/a.txt
        let a = find_child(root, "a.txt").expect("expected a.txt");
        assert!(!a.is_dir);
        assert_eq!(a.size, b"alpha\n".len() as u64);
        assert!(a.children.is_empty());

        // root/sub/
        let sub = find_child(root, "sub").expect("expected sub directory");
        assert!(sub.is_dir);
        assert_eq!(sub.size, 0);

        // root/sub/b.txt
        let b = find_child(sub, "b.txt").expect("expected b.txt");
        assert!(!b.is_dir);
        assert_eq!(b.size, b"beta\n".len() as u64);

        // root/sub/manifest.json
        let manifest = find_child(sub, "manifest.json").expect("expected manifest.json");
        assert!(!manifest.is_dir);
        assert_eq!(manifest.size, b"{}\n".len() as u64);

        // Verify actual extracted file contents on disk.
        let a_on_disk = out_dir.join("root").join("a.txt");
        assert_eq!(
            fs::read_to_string(&a_on_disk).unwrap(),
            "alpha\n",
            "extracted a.txt content must match"
        );
    }

    #[test]
    fn test_extract_corrupt_tar_gz_returns_error() {
        let tmp = tempfile_dir();
        // Write bytes that are NOT a valid gzip stream.
        let corrupt_path = tmp.join("corrupt.tar.gz");
        fs::write(&corrupt_path, b"this is definitely not a tar.gz file").unwrap();

        let out_dir = tmp.join("corrupt_out");
        let result = extract_tar_gz(
            corrupt_path.to_string_lossy().into_owned(),
            out_dir.to_string_lossy().into_owned(),
        );

        assert!(
            result.is_err(),
            "corrupt tar.gz must return an error, not a file tree"
        );
        let err = result.unwrap_err();
        assert!(
            !err.is_empty(),
            "error string must be non-empty for the frontend"
        );
        // Sanity: the error mentions the failure semantically.
        assert!(
            err.contains("Entpacken fehlgeschlagen")
                || err.contains("nicht lesbar")
                || err.to_lowercase().contains("invalid"),
            "error should describe the failure: got {err}"
        );
    }

    #[test]
    fn test_extract_missing_file_returns_error() {
        let tmp = tempfile_dir();
        let out_dir = tmp.join("missing_out");
        let result = extract_tar_gz(
            tmp.join("does_not_exist.tar.gz")
                .to_string_lossy()
                .into_owned(),
            out_dir.to_string_lossy().into_owned(),
        );
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("nicht gefunden"));
    }

    #[test]
    fn test_build_file_tree_on_plain_directory() {
        let tmp = tempfile_dir();
        // Build a small directory tree by hand.
        fs::create_dir_all(tmp.join("alpha")).unwrap();
        fs::write(tmp.join("alpha").join("one.txt"), b"1").unwrap();
        fs::write(tmp.join("two.txt"), b"22").unwrap();

        let tree = build_file_tree(&tmp).expect("build_file_tree should succeed");
        assert!(tree.is_dir);

        // alpha/ directory should come before two.txt (dirs first).
        assert_eq!(tree.children.len(), 2);
        assert_eq!(tree.children[0].name, "alpha");
        assert!(tree.children[0].is_dir);
        assert_eq!(tree.children[1].name, "two.txt");
        assert!(!tree.children[1].is_dir);
        assert_eq!(tree.children[1].size, 2);

        // alpha/one.txt
        assert_eq!(tree.children[0].children.len(), 1);
        assert_eq!(tree.children[0].children[0].name, "one.txt");
        assert_eq!(tree.children[0].children[0].size, 1);
    }

    #[test]
    fn test_build_file_tree_rejects_non_directory() {
        let tmp = tempfile_dir();
        let file_path = tmp.join("not_a_dir.txt");
        fs::write(&file_path, b"hi").unwrap();

        let result = build_file_tree(&file_path);
        assert!(result.is_err());
    }

    #[test]
    fn test_save_extracted_copies_all_files() {
        let tmp = tempfile_dir();
        let source = tmp.join("source");
        let target = tmp.join("target");

        // Source layout:
        //   source/
        //     hello.txt        -> "hello\n"
        //     nested/
        //       data.json     -> "{\"k\":1}\n"
        fs::create_dir_all(source.join("nested")).unwrap();
        fs::write(source.join("hello.txt"), b"hello\n").unwrap();
        fs::write(source.join("nested").join("data.json"), b"{\"k\":1}\n").unwrap();

        save_extracted(
            source.to_string_lossy().into_owned(),
            target.to_string_lossy().into_owned(),
        )
        .expect("save_extracted should succeed");

        // Verify all files copied with matching contents.
        assert_eq!(fs::read_to_string(target.join("hello.txt")).unwrap(), "hello\n");
        assert_eq!(
            fs::read_to_string(target.join("nested").join("data.json")).unwrap(),
            "{\"k\":1}\n"
        );

        // Target should not contain the source folder itself as a wrapper.
        assert!(!target.join("source").exists());
    }

    #[test]
    fn test_save_extracted_missing_source_returns_error() {
        let tmp = tempfile_dir();
        let result = save_extracted(
            tmp.join("nope").to_string_lossy().into_owned(),
            tmp.join("target").to_string_lossy().into_owned(),
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_save_as_zip_creates_valid_zip() {
        let tmp = tempfile_dir();
        let source = tmp.join("source");
        let zip_path = tmp.join("output.zip");

        // Source layout:
        //   source/
        //     hello.txt        -> "hello\n"
        //     nested/
        //       data.json     -> "{\"k\":1}\n"
        fs::create_dir_all(source.join("nested")).unwrap();
        fs::write(source.join("hello.txt"), b"hello\n").unwrap();
        fs::write(source.join("nested").join("data.json"), b"{\"k\":1}\n").unwrap();

        save_as_zip(
            source.to_string_lossy().into_owned(),
            zip_path.to_string_lossy().into_owned(),
        )
        .expect("save_as_zip should succeed");

        // Verify the ZIP file exists and has ZIP magic bytes
        assert!(zip_path.exists());
        let zip_bytes = fs::read(&zip_path).unwrap();
        assert!(!zip_bytes.is_empty(), "ZIP file should not be empty");
        assert_eq!(&zip_bytes[0..2], b"PK", "file must have ZIP magic bytes");

        // Read back the ZIP and verify entries
        let file = fs::File::open(&zip_path).unwrap();
        let mut archive = zip::ZipArchive::new(file).expect("should be a valid ZIP");

        let names: Vec<String> = (0..archive.len())
            .map(|i| archive.by_index(i).unwrap().name().to_string())
            .collect();
        assert!(
            names.contains(&"hello.txt".to_string()),
            "ZIP should contain hello.txt, got {:?}", names
        );
        assert!(
            names.contains(&"nested/data.json".to_string()),
            "ZIP should contain nested/data.json, got {:?}", names
        );

        // Verify content of hello.txt
        let mut hello_file = archive
            .by_name("hello.txt")
            .expect("hello.txt should be in ZIP");
        let mut content = String::new();
        hello_file.read_to_string(&mut content).unwrap();
        assert_eq!(content, "hello\n");
    }

    #[test]
    fn test_save_as_zip_missing_source_returns_error() {
        let tmp = tempfile_dir();
        let result = save_as_zip(
            tmp.join("nope").to_string_lossy().into_owned(),
            tmp.join("out.zip").to_string_lossy().into_owned(),
        );
        assert!(result.is_err());
    }

    #[test]
    fn test_read_text_file_returns_content() {
        let tmp = tempfile_dir();
        let file_path = tmp.join("note.txt");
        let content = "Entschluesselung erfolgreich\n";
        fs::write(&file_path, content).unwrap();

        let result = read_text_file(file_path.to_string_lossy().into_owned());
        assert!(result.is_ok());
        assert_eq!(result.unwrap(), content);
    }

    #[test]
    fn test_read_text_file_missing_returns_error() {
        let tmp = tempfile_dir();
        let result = read_text_file(
            tmp.join("ghost.txt")
                .to_string_lossy()
                .into_owned(),
        );
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("nicht gefunden"));
    }

    #[test]
    fn test_read_text_file_directory_returns_error() {
        let tmp = tempfile_dir();
        let dir_path = tmp.join("adir");
        fs::create_dir_all(&dir_path).unwrap();
        let result = read_text_file(dir_path.to_string_lossy().into_owned());
        assert!(result.is_err());
    }

    #[test]
    fn test_file_tree_serializes_to_json() {
        let tree = FileTree {
            name: "root".into(),
            path: "/tmp/root".into(),
            is_dir: true,
            size: 0,
            children: vec![FileTree {
                name: "a.txt".into(),
                path: "/tmp/root/a.txt".into(),
                is_dir: false,
                size: 5,
                children: Vec::new(),
            }],
        };
        let json = serde_json::to_string(&tree).expect("FileTree must serialize");
        assert!(json.contains("\"name\":\"root\""));
        assert!(json.contains("\"is_dir\":true"));
        assert!(json.contains("\"size\":5"));
        assert!(json.contains("\"children\":[]"));
    }

    // -----------------------------------------------------------------------
    // Temp-file management tests (VAL-CROSS-004)
    // -----------------------------------------------------------------------

    #[test]
    fn test_create_temp_dir_creates_and_tracks() {
        let path = create_temp_dir().expect("create_temp_dir should succeed");
        assert!(Path::new(&path).exists(), "temp dir must exist on disk");
        assert!(Path::new(&path).is_dir());

        // Tracked in the global list.
        let tracked = TEMP_DIRS.lock().unwrap().clone();
        assert!(tracked.contains(&path), "temp dir must be tracked for cleanup");

        // Clean up so the test doesn't leave artifacts.
        fs::remove_dir_all(&path).unwrap();
    }

    #[test]
    fn test_write_temp_file_writes_bytes() {
        let dir = create_temp_dir().expect("create_temp_dir should succeed");
        let data = vec![0x1fu8, 0x8b, 0x00, 0x01];
        let file_path =
            write_temp_file(dir.clone(), "test.tar.gz".into(), data.clone())
                .expect("write_temp_file should succeed");

        assert!(Path::new(&file_path).exists());
        let read = fs::read(&file_path).unwrap();
        assert_eq!(read, data);

        // Cleanup
        cleanup_temp_dir(dir).expect("cleanup should succeed");
        assert!(!Path::new(&file_path).exists(), "file must be deleted after cleanup");
    }

    #[test]
    fn test_cleanup_temp_dir_deletes_directory() {
        let dir = create_temp_dir().expect("create_temp_dir should succeed");
        fs::write(Path::new(&dir).join("nested.txt"), b"data").unwrap();
        fs::create_dir_all(Path::new(&dir).join("sub")).unwrap();
        fs::write(Path::new(&dir).join("sub").join("deep.txt"), b"deep").unwrap();

        cleanup_temp_dir(dir.clone()).expect("cleanup should succeed");
        assert!(!Path::new(&dir).exists(), "temp dir must not exist after cleanup");

        // Untracked after cleanup.
        let tracked = TEMP_DIRS.lock().unwrap().clone();
        assert!(!tracked.contains(&dir), "cleaned dir must be untracked");
    }

    #[test]
    fn test_cleanup_temp_dir_missing_path_is_ok() {
        // Cleaning up a non-existent path should not error (idempotent).
        let result = cleanup_temp_dir("/nonexistent/path/abc123".into());
        assert!(result.is_ok());
    }

    #[test]
    fn test_cleanup_all_temp_dirs_removes_everything() {
        let dir1 = create_temp_dir().unwrap();
        let dir2 = create_temp_dir().unwrap();
        fs::write(Path::new(&dir1).join("a.txt"), b"a").unwrap();
        fs::write(Path::new(&dir2).join("b.txt"), b"b").unwrap();

        cleanup_all_temp_dirs();

        assert!(!Path::new(&dir1).exists());
        assert!(!Path::new(&dir2).exists());
        assert!(TEMP_DIRS.lock().unwrap().is_empty());
    }

    #[test]
    fn test_write_temp_file_invalid_dir_returns_error() {
        let result = write_temp_file(
            "/nonexistent/dir/xyz".into(),
            "file.txt".into(),
            vec![1u8, 2u8],
        );
        assert!(result.is_err());
    }

    /// Simple unique temp dir helper (avoids pulling in the `tempfile`
    /// crate, keeping dependencies minimal per AGENTS.md KISS rules).
    fn tempfile_dir() -> PathBuf {
        let mut buf = [0u8; 16];
        use std::time::{SystemTime, UNIX_EPOCH};
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        buf.copy_from_slice(&nanos.to_le_bytes());
        let suffix: String = buf
            .iter()
            .map(|b| format!("{:02x}", b))
            .collect();
        let dir = std::env::temp_dir().join(format!("msm-rust-test-{}", suffix));
        fs::create_dir_all(&dir).unwrap();
        // Register cleanup on test process exit via a lazy guard.
        // Tests are short-lived; leak-guard is not required for correctness.
        dir
    }
}
