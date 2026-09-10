"""Sicherheits-Validatoren fuer Chat-Medien, Anhaenge und verschluesselte Blobs.

Enforces:
- MIME-Type & Magic Bytes Sniffing (nie blind der Endung oder Content-Type vertrauen)
- Zip-Bomb-Erkennung (Kompressionsverhaeltnis, Groessenlimits, Rekursion)
- SVG-Sanitization und Schutz vor SVG-basierten XSS-Payloads
- Speicher- und Groessenlimits (Riesen-Dateien-Schutz)
- E2EE-Blob-Validierung (Server darf nur verschluesselte Blobs sehen)
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import BinaryIO

# Speicher-Limits
MAX_MEDIA_BYTES = 25 * 1024 * 1024  # 25 MB fuer Anhaenge
MAX_IMAGE_BYTES = 8 * 1024 * 1024   # 8 MB fuer Bilder
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB fuer Sprachnachrichten
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MB maximal entpackt
MAX_ZIP_RATIO = 25.0  # Max. 25:1 Kompressionsrate (Schutz vor Zip-Bombs)
MAX_ZIP_ENTRIES = 1000  # Max. 1000 Dateien in einem Archiv

# Blockierte Executable / Shell / System Signaturen
BLOCKED_EXECUTABLE_SIGNATURES: tuple[bytes, ...] = (
    b"MZ",              # Windows PE/EXE/DLL
    b"\x7fELF",         # Linux ELF
    b"\xca\xfe\xba\xbe",# Java Bytecode / Mach-O Fat Binary
    b"\xce\xfa\xed\xfe",# Mach-O 32-bit
    b"\xcf\xfa\xed\xfe",# Mach-O 64-bit
    b"\xfe\xed\xfa\xce",# Mach-O 32-bit (reverse)
    b"\xfe\xed\xfa\xcf",# Mach-O 64-bit (reverse)
    b"#!",              # Shebang Script
)

# Gefaehrliche SVG-Elemente fuer XSS
DANGEROUS_SVG_TAGS: set[str] = {
    "script", "foreignobject", "iframe", "object", "embed",
    "applet", "meta", "link", "form", "input", "button", "base",
    "set", "animate", "animatetransform", "handler", "feimage", "use",
}

# Gefaehrliche Event-Handler und URI-Schemata
RE_SVG_EVENT_HANDLERS = re.compile(r"(?i)\bon\w+\s*=")
RE_DANGEROUS_URIS = re.compile(
    r"(?i)(javascript:|vbscript:|data:text/html|data:application/javascript|data:text/javascript)"
)
RE_XML_ENTITIES = re.compile(r"(?i)<!(?:entity|doctype)\b")


def _clean_css_string(css_text: str) -> str:
    """Bereinigt CSS gegen Obfuskation durch Kommentare und Backslash-Escapes."""
    no_comments = re.sub(r"/\*.*?\*/", "", css_text, flags=re.DOTALL)

    def _decode_css_escape(m: re.Match) -> str:
        hex_or_char = m.group(1).rstrip()
        try:
            code_point = int(hex_or_char, 16)
            if 0x20 <= code_point <= 0x7E:
                return chr(code_point)
        except ValueError:
            pass
        return hex_or_char

    no_escapes = re.sub(r"\\([0-9a-fA-F]{1,6}\s?|.)", _decode_css_escape, no_comments)
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]", "", no_escapes).lower()



class ChatMediaSecurityError(ValueError):
    """Basisklasse fuer Sicherheitsverletzungen bei Medienanhaengen."""
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code}] {detail}")


class MimeTypeSpoofingError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Dateityp stimmt nicht mit den Magic Bytes ueberein") -> None:
        super().__init__("CHAT_MEDIA_MIME_SPOOFING", detail)


class ExecutableBlockedError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Ausfuehrbare Dateien sind im Chat strikt untersagt") -> None:
        super().__init__("CHAT_MEDIA_EXECUTABLE_BLOCKED", detail)


class ZipBombDetectedError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Zip-Bomb oder manipulierte Archivstruktur erkannt") -> None:
        super().__init__("CHAT_MEDIA_ZIP_BOMB_DETECTED", detail)


class SvgXssDetectedError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Gefaehrliche Skript- oder XSS-Elemente in SVG erkannt") -> None:
        super().__init__("CHAT_MEDIA_SVG_XSS_DETECTED", detail)


class StorageLimitExceededError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Datei ueberschreitet das erlaubte Speicherlimit") -> None:
        super().__init__("CHAT_MEDIA_STORAGE_LIMIT_EXCEEDED", detail)


class PlaintextBlobRejectedError(ChatMediaSecurityError):
    def __init__(self, detail: str = "Server akzeptiert ausschliesslich clientseitig verschluesselte Blobs (E2EE)") -> None:
        super().__init__("CHAT_MEDIA_E2EE_ENCRYPTION_REQUIRED", detail)


def detect_mime_from_magic_bytes(data: bytes) -> str:
    """Bestimmt den MIME-Type zuverlaessig anhand von Magic Bytes."""
    if len(data) == 0:
        return "application/octet-stream"

    # Strip BOM vor Signaturpruefungen
    raw = data
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if raw.startswith(bom):
            raw = raw[len(bom):]
            break

    # 1. Executable- & Script-Pruefung
    for sig in BLOCKED_EXECUTABLE_SIGNATURES:
        if raw.startswith(sig):
            raise ExecutableBlockedError(f"Blockierte Signatur erkannt: {sig.hex()}")

    stripped_raw = raw.lstrip()
    if (
        stripped_raw.startswith((b"@echo", b"@ECHO", b"rem ", b"REM "))
        or stripped_raw.startswith(b"<?php")
        or stripped_raw.startswith(b"<script")
    ):
        raise ExecutableBlockedError("Skript- oder Batch-Signatur erkannt")

    # 2. Bilder
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        return "image/gif"
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"

    # 3. Audio & Video
    if len(raw) >= 12 and raw[4:8] == b"ftyp":
        brand = raw[8:12]
        if brand in (b"isom", b"iso2", b"mp41", b"mp42", b"M4A ", b"M4V "):
            return "video/mp4"
    if raw.startswith(b"\x1a\x45\xdf\xa3"):
        # WebM / MKV EBML Header
        return "audio/webm"
    if raw.startswith(b"OggS"):
        return "audio/ogg"
    if raw.startswith(b"ID3") or (len(raw) >= 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0):
        return "audio/mpeg"

    # 4. Dokumente & Archive
    if raw.startswith(b"%PDF"):
        return "application/pdf"
    if raw.startswith(b"PK\x03\x04") or raw.startswith(b"PK\x05\x06") or raw.startswith(b"PK\x07\x08"):
        return "application/zip"
    if raw.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if raw.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "application/x-7z-compressed"
    if raw.startswith(b"Rar!\x1a\x07"):
        return "application/vnd.rar"
    if raw.startswith(b"BZh"):
        return "application/x-bzip2"
    if raw.startswith(b"\xfd7zXZ\x00"):
        return "application/x-xz"
    if len(raw) >= 262 and raw[257:262] == b"ustar":
        return "application/x-tar"

    # 5. SVG Pruefung (Text mit <svg)
    stripped = raw[:1024].lstrip()
    if (stripped.startswith(b"<?xml") and b"<svg" in stripped) or stripped.startswith(b"<svg"):
        return "image/svg+xml"

    # 6. JSON
    if (stripped.startswith(b"{") or stripped.startswith(b"[")) and b"\x00" not in raw[:1024]:
        try:
            raw.decode("utf-8")
            return "application/json"
        except UnicodeDecodeError:
            pass

    # 7. Plain Text (Keine Null-Bytes, gueltiges UTF-8)
    if b"\x00" not in raw[:4096]:
        try:
            raw.decode("utf-8")
            return "text/plain"
        except UnicodeDecodeError:
            pass

    return "application/octet-stream"


def validate_file_mime_and_magic(
    data: bytes,
    declared_mime: str | None = None,
    declared_filename: str | None = None,
    allowed_types: set[str] | None = None,
) -> str:
    """Validiert Magic Bytes gegen deklarierte Typen und blockiert Spoofing sowie verbotene Dateiendungen."""
    if declared_filename:
        clean_fn = declared_filename.strip().lower()
        blocked_extensions = (
            ".exe", ".dll", ".bat", ".cmd", ".sh", ".msi", ".vbs", ".ps1",
            ".elf", ".com", ".scr", ".pif", ".jar", ".cpl", ".hta", ".wsf", ".php"
        )
        for ext in blocked_extensions:
            if clean_fn.endswith(ext):
                raise ExecutableBlockedError(f"Ausfuehrbare Dateiendung '{ext}' ist strikt untersagt.")

    detected = detect_mime_from_magic_bytes(data)

    if allowed_types and detected not in allowed_types:
        raise MimeTypeSpoofingError(
            f"Dateityp '{detected}' ist nicht in den erlaubten Typen enthalten."
        )

    if declared_mime and declared_mime not in (detected, "application/octet-stream"):
        # Weiche Abweichungen wie text/plain vs text/x-log ignorieren, aber
        # krasse Spoofs (z.B. als PDF oder PNG deklarierte EXE oder ZIP) hart abweisen
        is_both_image = declared_mime.startswith("image/") and detected.startswith("image/")
        is_both_audio = declared_mime.startswith("audio/") and detected.startswith("audio/")
        if not (is_both_image or is_both_audio):
            raise MimeTypeSpoofingError(
                f"MIME-Spoofing erkannt: Deklariert als '{declared_mime}', tatsaechlich '{detected}'."
            )

    return detected


def inspect_archive_for_zip_bomb(data: bytes) -> None:
    """Untersucht ZIP-, GZIP- und TAR-Archive vor der Annahme auf Zip-Bomb Merkmale und Path Traversal."""
    if data.startswith(b"\x1f\x8b"):
        # GZIP-Pruefung: Stream-Dekompression gegen Modulo-2^32 und ISIZE-Spoofing
        if len(data) < 18:
            raise ZipBombDetectedError("Beschaedigtes GZIP-Archiv")
        import struct
        import zlib
        try:
            # 1. Vorab-Check des ISIZE Headers (schneller Abbruch bei offensichtlichen Bomben)
            isize = struct.unpack("<I", data[-4:])[0]
            if isize > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ZipBombDetectedError(
                    f"GZIP entpackte Gesamtgroesse ({isize} Bytes) ueberschreitet Limit ({MAX_ZIP_UNCOMPRESSED_BYTES} Bytes)"
                )

            # 2. Sichere Dekompressions-Simulation via zlib Streaming (Schutz vor 2^32 Modulo-Wrap-Around)
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            total_uncompressed = 0
            chunk_size = 64 * 1024
            for i in range(0, len(data), chunk_size):
                chunk = data[i : i + chunk_size]
                out = decompressor.decompress(chunk, MAX_ZIP_UNCOMPRESSED_BYTES - total_uncompressed + 1)
                total_uncompressed += len(out)
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ZipBombDetectedError(
                        f"GZIP entpackte Gesamtgroesse ({total_uncompressed} Bytes) ueberschreitet Limit ({MAX_ZIP_UNCOMPRESSED_BYTES} Bytes)"
                    )
            ratio = total_uncompressed / max(len(data), 1)
            if ratio > MAX_ZIP_RATIO and total_uncompressed > 1024 * 1024:
                raise ZipBombDetectedError(
                    f"Verdaechtige GZIP-Kompressionsrate von {ratio:.1f}:1 erkannt (Limit: {MAX_ZIP_RATIO}:1)"
                )
        except zlib.error as exc:
            raise ZipBombDetectedError(f"Beschaedigtes oder manipuliertes GZIP-Archiv: {exc}") from exc
        return

    # TAR Archive (sowohl mit ustar Magic als auch Standard tarfile Format)
    import tarfile
    is_tar = (len(data) >= 262 and data[257:262] == b"ustar") or (len(data) >= 512 and tarfile.is_tarfile(io.BytesIO(data)))
    if is_tar:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                members = tf.getmembers()
                if len(members) > MAX_ZIP_ENTRIES:
                    raise ZipBombDetectedError(f"Archiv enthaelt {len(members)} Dateien (Limit: {MAX_ZIP_ENTRIES})")
                total_uncompressed = sum(m.size for m in members)
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ZipBombDetectedError(
                        f"Entpackte Gesamtgroesse ({total_uncompressed} Bytes) ueberschreitet Limit ({MAX_ZIP_UNCOMPRESSED_BYTES} Bytes)"
                    )
                for m in members:
                    parts = [p for p in m.name.replace("\\", "/").split("/") if p]
                    if ".." in parts or m.name.startswith("/") or (len(m.name) >= 2 and m.name[1] == ":") or m.issym() or m.islnk():
                        raise ZipBombDetectedError(f"Gefaehrlicher Pfad oder Symlink im Archiv erkannt: {m.name}")
                    fn_lower = m.name.lower()
                    if fn_lower.endswith((".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz")):
                        raise ZipBombDetectedError("Verschachtelte Archive (Zip-in-Zip) sind untersagt")
        except tarfile.TarError as e:
            raise ZipBombDetectedError(f"Beschaedigtes oder manipuliertes TAR-Archiv: {e}") from e
        return

    # ZIP Archive (Standard Magic, Central Directory Marker oder Polyglot)
    is_zip = (
        data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
        or b"PK\x05\x06" in data[-1024:]
        or zipfile.is_zipfile(io.BytesIO(data))
    )
    if not is_zip:
        return

    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            infolist = zf.infolist()
            if len(infolist) > MAX_ZIP_ENTRIES:
                raise ZipBombDetectedError(
                    f"Archiv enthaelt {len(infolist)} Dateien (Limit: {MAX_ZIP_ENTRIES})"
                )

            total_uncompressed = sum(info.file_size for info in infolist)

            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ZipBombDetectedError(
                    f"Entpackte Gesamtgroesse ({total_uncompressed} Bytes) ueberschreitet Limit ({MAX_ZIP_UNCOMPRESSED_BYTES} Bytes)"
                )

            # Check Kompressionsrate gegen tatsaechliche Archivgroesse (immun gegen Header-Spoofing)
            ratio = total_uncompressed / max(len(data), 1)
            if ratio > MAX_ZIP_RATIO and total_uncompressed > 1024 * 1024:
                raise ZipBombDetectedError(
                    f"Verdaechtige Kompressionsrate von {ratio:.1f}:1 erkannt (Limit: {MAX_ZIP_RATIO}:1)"
                )

            # Check auf verschachtelte Archive und Path Traversal (Zip Slip)
            for info in infolist:
                fn = info.filename.replace("\\", "/")
                # Zip-Slip / Path Traversal Schutz
                parts = [p for p in fn.split("/") if p]
                if ".." in parts or fn.startswith("/") or (len(fn) >= 2 and fn[1] == ":"):
                    raise ZipBombDetectedError(f"Path Traversal (Zip-Slip) im Dateipfad erkannt: {info.filename}")

                fn_lower = fn.lower()
                if fn_lower.endswith((".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz")):
                    raise ZipBombDetectedError("Verschachtelte Archive (Zip-in-Zip) sind untersagt")

    except zipfile.BadZipFile as e:
        raise ZipBombDetectedError(f"Beschaedigtes oder manipuliertes ZIP-Archiv: {e}") from e


def sanitize_svg_content(svg_data: str | bytes) -> str:
    """Prueft und bereinigt SVG-Inhalte gegen XSS-Vektoren.
    
    Blockiert:
    - Externe Entitaeten / DTD (XXE / Billion Laughs)
    - Skript-Tags (<script>) und Animationsinjektionen (<set>, <animate>)
    - Inline Event-Handler (onload, onerror, onclick, etc.)
    - Gefaehrliche URI-Schemata (javascript:, vbscript:, data:text/html, etc.)
    - foreignObject, iframe, embed, object, use, feImage
    """
    if isinstance(svg_data, bytes):
        try:
            svg_text = svg_data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SvgXssDetectedError("SVG enthaelt kein gueltiges UTF-8") from exc
    else:
        svg_text = svg_data

    # 1. XXE / DTD Check
    if RE_XML_ENTITIES.search(svg_text):
        raise SvgXssDetectedError("SVG darf keine DTD- oder ENTITY-Definitionen enthalten (XXE-Schutz)")

    # 2. Event-Handler Check
    if RE_SVG_EVENT_HANDLERS.search(svg_text):
        raise SvgXssDetectedError("Inline Event-Handler (on...) in SVG sind strikt untersagt")

    # 3. Gefaehrliche URIs
    if RE_DANGEROUS_URIS.search(svg_text):
        raise SvgXssDetectedError("Gefaehrliche URI-Schemata (javascript:, etc.) in SVG gefunden")

    # 4. XML-Parser-Pruefung fuer verbotene Tags
    try:
        parser = ET.XMLParser()
        root = ET.fromstring(svg_text, parser=parser)
    except ET.ParseError as e:
        raise SvgXssDetectedError(f"Ungueltiges SVG-XML: {e}") from e

    # Tag-Pruefung rekursiv
    for elem in root.iter():
        tag_name = elem.tag.split("}")[-1].lower() if "}" in elem.tag else elem.tag.lower()
        if tag_name in DANGEROUS_SVG_TAGS:
            raise SvgXssDetectedError(f"Verbotenes Element <{tag_name}> in SVG gefunden")

        if tag_name == "style":
            style_text = (elem.text or "") + (elem.tail or "")
            clean_style = _clean_css_string(style_text)
            if (
                "javascript:" in clean_style
                or "expression(" in clean_style
                or "behavior:" in clean_style
                or "@import" in clean_style
                or "-moz-binding" in clean_style
                or "url(data:text/html" in clean_style
                or "url(data:application/javascript" in clean_style
                or "url(data:text/javascript" in clean_style
            ):
                raise SvgXssDetectedError("Gefaehrliche CSS-Skripte oder Imports im SVG <style>-Element erkannt")

        for attr_name, attr_val in elem.attrib.items():
            attr_clean = attr_name.split("}")[-1].lower() if "}" in attr_name else attr_name.lower()
            if attr_clean.startswith("on"):
                raise SvgXssDetectedError(f"Verbotenes Attribut {attr_name} in SVG gefunden")
            
            # Bereinige Whitespace, Steuerzeichen und CSS Obfuskation
            val_clean = re.sub(r"[\s\x00-\x1f\x7f-\x9f]", "", str(attr_val)).lower()
            if (
                "javascript:" in val_clean
                or "vbscript:" in val_clean
                or "data:text/html" in val_clean
                or "data:text/javascript" in val_clean
                or "data:application/javascript" in val_clean
            ):
                raise SvgXssDetectedError(f"Gefaehrlicher Link in Attribut {attr_name}")

            if attr_clean == "style":
                val_style_clean = _clean_css_string(str(attr_val))
                if (
                    "javascript:" in val_style_clean
                    or "expression(" in val_style_clean
                    or "behavior:" in val_style_clean
                    or "@import" in val_style_clean
                    or "-moz-binding" in val_style_clean
                ):
                    raise SvgXssDetectedError("Gefaehrliche CSS-Inhalte im style-Attribut erkannt")

            # Bei a / image / use: Pruefe zusaetzlich href / xlink:href
            if attr_clean in ("href", "src"):
                if val_clean.startswith("data:") and not val_clean.startswith("data:image/"):
                    raise SvgXssDetectedError(f"Unerlaubtes Data-URI-Schema in Attribut {attr_name}")

    return svg_text


def sanitize_attachment_filename(file_name: str | None) -> str:
    """Bereinigt Dateinamen fuer Anhaenge gegen Path Traversal, CRLF, NTFS ADS und Injektionen."""
    if not file_name or not file_name.strip():
        return "attachment.bin"

    clean = file_name.strip().replace("\\", "/")
    # Nur Dateiname ohne Pfadangaben
    clean = clean.split("/")[-1]
    # Entferne Null-Bytes und Steuerzeichen (CRLF Injection Schutz)
    clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", clean)
    # Entferne Quotes, Semicolon, Colon (NTFS ADS) und gefaehrliche Zeichen
    clean = clean.replace('"', "").replace("'", "").replace(";", "_").replace(":", "_")
    # Windows Geraetenamen blockieren (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    name_root = clean.split(".")[0].upper()
    if name_root in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        clean = f"msm_{clean}"

    clean = clean.strip(". ")[:200]
    return clean or "attachment.bin"


def validate_encrypted_blob_payload(ciphertext_blob: str, max_bytes: int = MAX_MEDIA_BYTES) -> None:
    """Stellt sicher, dass ein hochgeladener Anhang tatsaechlich ein E2EE-Blob ist.
    
    Invariante:
    - Der Server darf NIEMALS Klartext-Dateien fuer den E2EE-Chat entgegennehmen.
    - Der Client muss vor dem Upload verschluesseln.
    - Prueft Groessenlimits und gueltige Blob-Struktur (z.B. sv-blob-v1: oder JSON-Envelope).
    """
    clean_blob = ciphertext_blob.strip()
    if not clean_blob:
        raise PlaintextBlobRejectedError("Leerer Ciphertext-Blob")

    if len(clean_blob) > max_bytes:
        raise StorageLimitExceededError(
            f"Verschluesselter Blob ueberschreitet Maximalgroesse ({len(clean_blob)} > {max_bytes} Zeichen)"
        )

    byte_len = len(clean_blob.encode("utf-8"))
    if byte_len > max_bytes:
        raise StorageLimitExceededError(
            f"Verschluesselter Blob ueberschreitet Maximalgroesse ({byte_len} > {max_bytes} Bytes)"
        )

    # 1. Erkennung von unverschluesseltem Klartext in der auesseren Zeichenkette
    raw_preview = clean_blob[:128].encode("utf-8", errors="ignore")
    for sig in BLOCKED_EXECUTABLE_SIGNATURES:
        if raw_preview.startswith(sig):
            raise ExecutableBlockedError("Hochgeladene Daten enthalten unverschluesselte ausfuehrbare Signaturen")

    if (
        raw_preview.startswith(b"\x89PNG")
        or raw_preview.startswith(b"\xff\xd8\xff")
        or raw_preview.startswith(b"GIF8")
        or raw_preview.startswith(b"%PDF")
        or raw_preview.startswith(b"PK\x03\x04")
        or raw_preview.startswith(b"\x1f\x8b")
        or (len(raw_preview) >= 12 and raw_preview[:4] == b"RIFF" and raw_preview[8:12] == b"WEBP")
    ):
        raise PlaintextBlobRejectedError(
            "Klartext-Medien-Signatur erkannt. Chat-Medien muessen clientseitig E2EE verschluesselt werden!"
        )

    # 2. Umschlagformat pruefen (sv-blob-v1:, sv-e2ee-..., JSON-Envelope)
    payload_to_inspect: str = ""
    is_prefixed = False
    for pfx in ("sv-blob-v1:", "sv-e2ee-v1:", "sv-e2ee-group-v1:", "sv-e2ee-team-v1:", "sv-file-manifest-v1:"):
        if clean_blob.startswith(pfx):
            payload_to_inspect = clean_blob[len(pfx):].strip()
            is_prefixed = True
            break
    if not is_prefixed and clean_blob.startswith("sv-"):
        parts = clean_blob.split(":", 1)
        if len(parts) == 2:
            payload_to_inspect = parts[1].strip()
            is_prefixed = True

    is_json_blob = clean_blob.startswith("{") and ("ciphertext" in clean_blob or "encrypted" in clean_blob)

    if not (is_prefixed or is_json_blob):
        raise PlaintextBlobRejectedError(
            "Ungueltiges Format fuer verschluesselten Chat-Blob (erwartet sv-blob-v1: oder JSON-Envelope)"
        )

    # 3. Payload-Inspektion: Was hinter dem Prefix liegt, darf kein unverschluesselter Klartext sein
    import base64
    candidate_bytes: bytes | None = None

    if is_json_blob:
        import json
        try:
            parsed_json = json.loads(clean_blob)
            ct = parsed_json.get("ciphertext") or parsed_json.get("encrypted") or ""
            if isinstance(ct, str) and ct:
                candidate_bytes = base64.b64decode(ct, validate=False)
        except Exception:
            pass
    elif is_prefixed:
        if not payload_to_inspect:
            raise PlaintextBlobRejectedError("Leere Nutzlast im verschluesselten Umschlag")

        # Direkt auf blockierte Executable-Signaturen in der unverschluesselten Nutzlast pruefen
        payload_raw = payload_to_inspect.encode("latin-1", errors="ignore")
        for sig in BLOCKED_EXECUTABLE_SIGNATURES:
            if payload_raw.startswith(sig):
                raise ExecutableBlockedError(f"Blockierte ausfuehrbare Signatur in Umschlag-Nutzlast erkannt: {sig.hex()}")
        if (
            payload_raw.startswith(b"\x89PNG")
            or payload_raw.startswith(b"\xff\xd8\xff")
            or payload_raw.startswith(b"GIF8")
            or payload_raw.startswith(b"%PDF")
            or payload_raw.startswith(b"PK\x03\x04")
            or payload_raw.startswith(b"\x1f\x8b")
            or (len(payload_raw) >= 12 and payload_raw[:4] == b"RIFF" and payload_raw[8:12] == b"WEBP")
        ):
            raise PlaintextBlobRejectedError("Klartext-Medien-Signatur in Umschlag-Nutzlast erkannt")

        # Wenn die Nutzlast direkte Klartext-Tags oder Markup enthaelt
        lower_payload = payload_to_inspect.lower()
        if (
            lower_payload.startswith(("<svg", "<?xml", "<!doctype", "<html", "{", "["))
            or any(sig in lower_payload[:64] for sig in ("<script", "onload=", "onerror="))
        ):
            raise PlaintextBlobRejectedError("Klartext-Markup im verschluesselten Umschlag erkannt")

        # Pruefe auf Parameter-Format (z.B. "AES-GCM-256:iv=...:tag=...:ciphertext=..." oder "AES-GCM-256:...")
        if ":" in payload_to_inspect:
            ct_match = re.search(r"ciphertext=([A-Za-z0-9+/=_-]+)", payload_to_inspect)
            if ct_match:
                try:
                    candidate_bytes = base64.b64decode(ct_match.group(1), validate=False)
                except Exception:
                    pass
            else:
                parts = payload_to_inspect.split(":")
                candidate_part = parts[-1]
                if all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_\n\r" for c in candidate_part[:128]):
                    try:
                        candidate_bytes = base64.b64decode(candidate_part[:512], validate=False)
                    except Exception:
                        pass
        elif all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_\n\r" for c in payload_to_inspect[:128]):
            # Standard Base64 Payload aus encryptE2eeAttachmentBlob
            try:
                candidate_bytes = base64.b64decode(payload_to_inspect[:512], validate=False)
            except Exception:
                pass
        else:
            # Enthaelt ungueltige Zeichen wie Leerzeichen oder nicht-Base64 Zeichen
            if " " in payload_to_inspect:
                raise PlaintextBlobRejectedError("Umschlag enthaelt unverschluesselten Klartext statt Base64-Ciphertext")
            raise PlaintextBlobRejectedError("Umschlag-Nutzlast ist weder gueltiger Base64-Ciphertext noch ein gueltiger Parameter-Envelope")

    # 4. Wenn candidate_bytes dekodiert werden konnten, pruefe auf Signaturen
    if candidate_bytes:
        for sig in BLOCKED_EXECUTABLE_SIGNATURES:
            if candidate_bytes.startswith(sig):
                raise ExecutableBlockedError("Dekodierte Daten enthalten ausfuehrbare Signaturen")

        if (
            candidate_bytes.startswith(b"\x89PNG")
            or candidate_bytes.startswith(b"\xff\xd8\xff")
            or candidate_bytes.startswith(b"GIF8")
            or candidate_bytes.startswith(b"%PDF")
            or candidate_bytes.startswith(b"PK\x03\x04")
            or candidate_bytes.startswith(b"\x1f\x8b")
            or (len(candidate_bytes) >= 12 and candidate_bytes[:4] == b"RIFF" and candidate_bytes[8:12] == b"WEBP")
            or candidate_bytes.lstrip().startswith(b"<svg")
            or candidate_bytes.lstrip().startswith(b"<?xml")
            or candidate_bytes.lstrip().startswith(b"<!DOCTYPE")
            or candidate_bytes.lstrip().startswith(b"<html")
        ):
            raise PlaintextBlobRejectedError(
                "Klartext-Medien-Signatur in Payload erkannt. Medien muessen mit AES-GCM verschluesselt sein!"
            )



def validate_story_media_url(media_url: str | None) -> str | None:
    """Validiert Media-URLs fuer Story-Vorschauen gegen XSS, MIME-Spoofing und Riesen-Dateien.
    
    Erlaubte Schemata:
    - None / Leer -> None
    - Sichere HTTP(S)-URLs (http://, https://) ohne bösartige Sonderzeichen / Steuerzeichen
    - Relative API-Pfade auf Chat-Medien (/api/social/media/...)
    - Inline Data-URIs fuer Bilder (PNG, JPEG, WebP, GIF, SVG):
      * Base64-Prüfung und Längenbeschränkung auf MAX_IMAGE_BYTES (8 MB)
      * Magic Bytes Prüfung bei Binärbildern
      * Vollständige SVG-Sanitization gegen XSS (<script>, onerror, javascript:, etc.)
    """
    if not media_url or not media_url.strip():
        return None

    clean_url = media_url.strip()
    lower = clean_url.lower()

    # 1. Blockiere offensichtlich gefährliche Schemata
    if lower.startswith((
        "javascript:",
        "vbscript:",
        "data:text/html",
        "data:text/javascript",
        "data:application/javascript",
    )):
        raise SvgXssDetectedError("Gefaehrliches URI-Schema in Story-Media-URL erkannt")

    # 2. Data-URI Validierung
    if lower.startswith("data:"):
        allowed_prefixes = (
            "data:image/png",
            "data:image/jpeg",
            "data:image/jpg",
            "data:image/webp",
            "data:image/gif",
            "data:image/svg+xml",
        )
        if not any(lower.startswith(prefix) for prefix in allowed_prefixes):
            raise MimeTypeSpoofingError("Fuer Stories sind nur Bildformate (PNG, JPEG, WebP, GIF, SVG) erlaubt")

        parts = clean_url.split(",", 1)
        if len(parts) != 2:
            raise ChatMediaSecurityError("CHAT_MEDIA_MALFORMED_DATA_URI", "Ungueltiges Data-URI Format")

        header, payload = parts[0], parts[1]
        is_svg = "image/svg+xml" in header.lower()

        if ";base64" in header.lower():
            import base64
            try:
                raw_bytes = base64.b64decode(payload)
            except Exception as e:
                raise ChatMediaSecurityError("CHAT_MEDIA_INVALID_BASE64", f"Ungueltiges Base64 in Data-URI: {e}")

            if len(raw_bytes) > MAX_IMAGE_BYTES:
                raise StorageLimitExceededError(f"Story-Bild ueberschreitet Maximalgroesse von {MAX_IMAGE_BYTES // (1024*1024)}MB")

            if is_svg:
                sanitize_svg_content(raw_bytes)
            else:
                validate_file_mime_and_magic(
                    raw_bytes,
                    allowed_types={"image/png", "image/jpeg", "image/webp", "image/gif"}
                )
        else:
            # Klartext/URL-kodiertes SVG
            if not is_svg:
                raise MimeTypeSpoofingError("Nicht-Base64 Data-URIs sind nur fuer SVG zulaessig")
            import urllib.parse
            unquoted = urllib.parse.unquote(payload)
            if len(unquoted.encode("utf-8")) > MAX_IMAGE_BYTES:
                raise StorageLimitExceededError("Story-SVG ueberschreitet Maximalgroesse")
            sanitize_svg_content(unquoted)

        return clean_url

    # 3. HTTP, HTTPS und relative API-URLs
    if lower.startswith(("http://", "https://", "/api/social/media/", "/uploads/", "/static/")):
        # Keine Steuerzeichen, Newlines oder Whitespace
        if any(c in clean_url for c in ("\r", "\n", "\t", " ", "<", ">", '"', "'", "\\")):
            raise ChatMediaSecurityError("CHAT_MEDIA_INVALID_URL", "Ungueltige Zeichen in Story-Media-URL")
        return clean_url

    raise ChatMediaSecurityError(
        "CHAT_MEDIA_UNSUPPORTED_SCHEME",
        "Story-Media-URL muss eine gueltige Bild-URL (http, https, /api/social/media/...) oder ein sicheres Data-URI sein"
    )
