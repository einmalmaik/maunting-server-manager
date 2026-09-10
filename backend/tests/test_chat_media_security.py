from __future__ import annotations

import base64
import io
import time
import zipfile
from datetime import datetime, timezone
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import User, ChatMedia, ChatGroup, ChatGroupMember, DirectChat, UserFriend
from services.auth_service import AuthService
from services.social_service import SocialService
from services.chat_media_service import ChatMediaService
from services.chat_media_validator import (
    MAX_MEDIA_BYTES,
    MAX_IMAGE_BYTES,
    detect_mime_from_magic_bytes,
    validate_file_mime_and_magic,
    inspect_archive_for_zip_bomb,
    sanitize_svg_content,
    sanitize_attachment_filename,
    validate_encrypted_blob_payload,
    validate_story_media_url,
    MimeTypeSpoofingError,
    ExecutableBlockedError,
    ZipBombDetectedError,
    SvgXssDetectedError,
    StorageLimitExceededError,
    PlaintextBlobRejectedError,
)


# ---------------------------------------------------------------------------
# 1. MIME-Type & Extension Sniffing (Magic Bytes)
# ---------------------------------------------------------------------------

def test_detect_mime_magic_bytes_valid_formats():
    """Prueft korrekte Erkennung binaerer Dateiformate unabhaengig von Dateiendungen."""
    # PNG
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(png_bytes) == "image/png"

    # JPEG
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(jpeg_bytes) == "image/jpeg"

    # GIF
    gif_bytes = b"GIF89a" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(gif_bytes) == "image/gif"

    # WEBP
    webp_bytes = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    assert detect_mime_from_magic_bytes(webp_bytes) == "image/webp"

    # PDF
    pdf_bytes = b"%PDF-1.4\n" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(pdf_bytes) == "application/pdf"

    # MP4
    mp4_bytes = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
    assert detect_mime_from_magic_bytes(mp4_bytes) == "video/mp4"

    # WebM
    webm_bytes = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(webm_bytes) == "audio/webm"

    # OGG
    ogg_bytes = b"OggS\x00\x02" + b"\x00" * 32
    assert detect_mime_from_magic_bytes(ogg_bytes) == "audio/ogg"


def test_block_executable_magic_bytes():
    """Stellt sicher, dass blockierte Executable-Signaturen hart abgewiesen werden."""
    # Windows PE / EXE (MZ)
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(b"MZ\x90\x00\x03\x00\x00\x00")

    # Linux ELF
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(b"\x7fELF\x02\x01\x01\x00")

    # Mach-O Fat / Java Class
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(b"\xca\xfe\xba\xbe\x00\x00")

    # Shebang Script
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(b"#!/bin/bash\nrm -rf /")


def test_mime_spoofing_detection():
    """Erkennt und blockiert Versuche, Dateitypen zu fälschen."""
    # Als PNG deklariert, aber tatsaechlich Text
    fake_png = b"Not a real PNG header, just plain text"
    with pytest.raises(MimeTypeSpoofingError):
        validate_file_mime_and_magic(fake_png, declared_mime="image/png")

    # Als Bild deklariert, aber tatsaechlich ein ZIP
    zip_bytes = b"PK\x03\x04" + b"\x00" * 30
    with pytest.raises(MimeTypeSpoofingError):
        validate_file_mime_and_magic(zip_bytes, declared_mime="image/png")

    # Unerlaubter Typ in Whitelist
    pdf_bytes = b"%PDF-1.7\n"
    with pytest.raises(MimeTypeSpoofingError):
        validate_file_mime_and_magic(pdf_bytes, allowed_types={"image/png", "image/jpeg"})


# ---------------------------------------------------------------------------
# 2. Zip-Bomb Erkennung & Archiv-Inspektion
# ---------------------------------------------------------------------------

def test_zip_bomb_detection_and_safety():
    """Untersucht ZIP-Archive auf Gefaehrdungspotential (Kompressionsrate, Rekursion)."""
    # 1. Gueltiges kleines ZIP-Archiv darf passieren
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test.txt", "Hello MSM World!")
    small_zip = bio.getvalue()
    inspect_archive_for_zip_bomb(small_zip)  # darf keine Exception werfen

    # 2. Hohe Kompressionsrate (Zip-Bomb-Muster: viele Nullen hoch komprimiert)
    bomb_io = io.BytesIO()
    with zipfile.ZipFile(bomb_io, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge_sparse.txt", b"\x00" * (2 * 1024 * 1024))
    bomb_zip = bomb_io.getvalue()
    with pytest.raises(ZipBombDetectedError):
        inspect_archive_for_zip_bomb(bomb_zip)

    # 3. Verschachteltes Archiv (Zip-in-Zip)
    nested_io = io.BytesIO()
    with zipfile.ZipFile(nested_io, "w") as zf:
        zf.writestr("inner.zip", b"PK\x03\x04\x00")
    nested_zip = nested_io.getvalue()
    with pytest.raises(ZipBombDetectedError, match="Verschachtelte Archive"):
        inspect_archive_for_zip_bomb(nested_zip)

    # 4. Beschaedigte ZIP-Struktur
    corrupt_zip = b"PK\x03\x04corrupted_payload_that_is_invalid"
    with pytest.raises(ZipBombDetectedError):
        inspect_archive_for_zip_bomb(corrupt_zip)

    # 5. Zip-Slip (Path Traversal / Escape out of extraction directory)
    slip_io = io.BytesIO()
    with zipfile.ZipFile(slip_io, "w") as zf:
        zf.writestr("../../etc/passwd", "root:x:0:0")
    slip_zip = slip_io.getvalue()
    with pytest.raises(ZipBombDetectedError, match="Zip-Slip"):
        inspect_archive_for_zip_bomb(slip_zip)

    # 6. GZIP Archive und Bomb Detection
    import gzip
    gz_io = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_io, mode="wb") as gz:
        gz.write(b"\x00" * (60 * 1024 * 1024))
    gz_bomb = gz_io.getvalue()
    with pytest.raises(ZipBombDetectedError):
        inspect_archive_for_zip_bomb(gz_bomb)


# ---------------------------------------------------------------------------
# 3. SVG-Sanitization & Schutz vor SVG-XSS
# ---------------------------------------------------------------------------

def test_svg_sanitization_xss_protection():
    """Verhindert SVG-basierte XSS-Attacken und XXE-Exploits."""
    # 1. Clean SVG
    clean_svg = '<svg viewBox="0 0 10 10" xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/></svg>'
    res = sanitize_svg_content(clean_svg)
    assert "<circle" in res

    # 2. Block <script> Tag
    evil_script = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_script)

    # 3. Block Inline Event Handler (onload, onerror, onclick)
    evil_onload = '<svg onload="alert(1)" xmlns="http://www.w3.org/2000/svg"><text>Hi</text></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_onload)

    evil_onerror = '<svg xmlns="http://www.w3.org/2000/svg"><image href="bad" onerror="alert(2)"/></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_onerror)

    # 4. Block javascript: URI
    evil_js_href = '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(document.cookie)"><text>Click</text></a></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_js_href)

    # 5. Block XXE / DTD Injections
    evil_xxe = '<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_xxe)

    # 6. Block <set> und <animate> Event- / Attribut-Manipulation
    evil_set = '<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/><set attributeName="onmouseover" to="alert(1)"/></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_set)

    evil_animate = '<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="href" values="javascript:alert(1)"/></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_animate)

    # 7. Obfuskierte Whitespace/Carriage Return URIs
    evil_cr = '<svg xmlns="http://www.w3.org/2000/svg"><a href="java\rscript:alert(1)"><text>test</text></a></svg>'
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_cr)


# ---------------------------------------------------------------------------
# 4. Client-seitige Verschlüsselung: Server akzeptiert nur E2EE Blobs
# ---------------------------------------------------------------------------

def test_validate_encrypted_blob_payload():
    """Server darf NIEMALS unverschluesselte Rohdaten annehmen."""
    # 1. Gueltiger E2EE-Blob mit sv-blob-v1: Praefix
    valid_blob = "sv-blob-v1:AES-GCM-256:iv=abcdef123456:tag=987654:ciphertext=kjasdfiuwbefiqwbefoiqbwfiub"
    validate_encrypted_blob_payload(valid_blob)

    # 2. Gueltiger JSON Envelope
    valid_json_envelope = '{"ciphertext":"abc123xyz","iv":"def456","tag":"ghi789"}'
    validate_encrypted_blob_payload(valid_json_envelope)

    # 3. Ablehnung von Klartext-Bildern (PNG Magic Bytes)
    plaintext_png = "\x89PNG\r\n\x1a\nThis is raw png data"
    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload(plaintext_png)

    # 4. Ablehnung von Klartext-PDF
    plaintext_pdf = "%PDF-1.4\nSome raw PDF content"
    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload(plaintext_pdf)

    # 5. Ablehnung von Klartext-Executables
    plaintext_exe = "MZ\x90\x00Executable binary"
    with pytest.raises(ExecutableBlockedError):
        validate_encrypted_blob_payload(plaintext_exe)

    # 6. Ablehnung von leeren Blobs
    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload("   ")

    # 7. Ueberschreitung von Groessenlimits
    huge_blob = "sv-blob-v1:" + ("A" * (MAX_MEDIA_BYTES + 10))
    with pytest.raises(StorageLimitExceededError):
        validate_encrypted_blob_payload(huge_blob)

    # 8. Unverschluesselte Daten getarnt hinter sv-blob-v1: Praefix
    with pytest.raises(ExecutableBlockedError):
        validate_encrypted_blob_payload("sv-blob-v1:MZ\x90\x00BinaryExePayloadHere")

    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload("sv-blob-v1:<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")

    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload("sv-blob-v1:PK\x03\x04ZipFileHeaderHere")

    # 9. Base64-kodierte unverschluesselte Executable / Media
    b64_exe = "sv-blob-v1:" + base64.b64encode(b"MZ\x90\x00ExecutableWindowsBinary").decode("ascii")
    with pytest.raises(ExecutableBlockedError):
        validate_encrypted_blob_payload(b64_exe)

    b64_pdf = "sv-blob-v1:" + base64.b64encode(b"%PDF-1.4\nUnencrypted document").decode("ascii")
    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload(b64_pdf)

    # 10. Reiner unverschluesselter englischer Text ohne Envelope
    with pytest.raises(PlaintextBlobRejectedError):
        validate_encrypted_blob_payload("This is an unencrypted secret message without any encryption")


# ---------------------------------------------------------------------------
# 5. Story Media URL Validierung & XSS-Schutz
# ---------------------------------------------------------------------------

def test_validate_story_media_url():
    """Prueft die Absicherung oeffentlicher Status-Stories gegen Injection."""
    # Gueltige HTTP / HTTPS / Relative Pfade
    assert validate_story_media_url(None) is None
    assert validate_story_media_url("https://cdn.example.com/story.jpg") == "https://cdn.example.com/story.jpg"
    assert validate_story_media_url("/api/social/media/test-id/download") == "/api/social/media/test-id/download"

    # Blockiert javascript: URLs
    with pytest.raises(SvgXssDetectedError):
        validate_story_media_url("javascript:alert('xss')")

    # Blockiert unzulaessige URL-Zeichen
    with pytest.raises(Exception):
        validate_story_media_url("https://example.com/img<script>.jpg")

    # Gueltige PNG Data-URI
    valid_png_raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    valid_data_uri = "data:image/png;base64," + base64.b64encode(valid_png_raw).decode("ascii")
    assert validate_story_media_url(valid_data_uri) == valid_data_uri

    # Data-URI mit gefaelschtem MIME-Type (als PNG deklariert, aber Text)
    fake_png_data_uri = "data:image/png;base64," + base64.b64encode(b"Not a real PNG").decode("ascii")
    with pytest.raises(MimeTypeSpoofingError):
        validate_story_media_url(fake_png_data_uri)

    # Data-URI mit SVG-XSS Payload
    evil_svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    evil_svg_data_uri = "data:image/svg+xml;base64," + base64.b64encode(evil_svg.encode("utf-8")).decode("ascii")
    with pytest.raises(SvgXssDetectedError):
        validate_story_media_url(evil_svg_data_uri)


def test_social_service_create_story_security(db: Session, owner_user: User):
    """Prueft, dass SocialService.create_story bösartige Anhänge verweigert."""
    # 1. Gueltige Story
    story = SocialService.create_story(
        db,
        user=owner_user,
        content="Gültige Story",
        media_url="https://images.example.com/nature.jpg",
    )
    assert story.id is not None
    assert story.media_url == "https://images.example.com/nature.jpg"

    # 2. Boesartige javascript: URL wird mit 422 abgewiesen
    with pytest.raises(HTTPException) as exc_info:
        SocialService.create_story(
            db,
            user=owner_user,
            content="Boesartige Story",
            media_url="javascript:alert(1)",
        )
    assert exc_info.value.status_code == 422

    # 3. SVG mit Skript in Data-URI wird abgewiesen
    evil_svg = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    evil_uri = "data:image/svg+xml;base64," + base64.b64encode(evil_svg.encode("utf-8")).decode("ascii")
    with pytest.raises(HTTPException) as exc_info:
        SocialService.create_story(
            db,
            user=owner_user,
            content="SVG XSS Story",
            media_url=evil_uri,
        )
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# 6. E2EE Upload, Signierte URLs, Expiration & Chat-Mitgliedschaft
# ---------------------------------------------------------------------------

def test_chat_media_upload_and_signed_url_flow(db: Session, owner_user: User, regular_user: User):
    """Prueft den vollstaendigen Lebenszyklus eines E2EE-Anhangs mit Mitgliedschaftspruefung."""
    # 1. Bereite Direktchat vor (Freundschaft und DirectChat)
    friend_req = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, friend_req["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)
    blind_box = chat.blind_mailbox_id

    # 2. Owner laedt verschluesselten Blob hoch
    ciphertext = "sv-blob-v1:AES-GCM:iv=12345678:tag=87654321:ciphertext=EncryptedPayloadBytesXYZ"
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=owner_user,
        blind_mailbox_id=blind_box,
        ciphertext_blob=ciphertext,
        file_name="geheimdokument.pdf",
        media_type="application/pdf",
    )
    assert media.id is not None
    assert media.ciphertext_blob == ciphertext
    assert media.size_bytes == len(ciphertext.encode("utf-8"))

    # 3. Owner erzeugt signierte URL (erfolgreich)
    signed_url, expires_at = ChatMediaService.generate_signed_url(
        db, user=owner_user, media_id=media.id, ttl_seconds=600
    )
    assert f"/api/social/media/{media.id}/download" in signed_url
    assert "token=" in signed_url
    assert "expires=" in signed_url

    # 4. Chat-Partner (regular_user) darf ebenfalls signierte URL anfordern (da Chat-Mitglied)
    partner_url, partner_exp = ChatMediaService.generate_signed_url(
        db, user=regular_user, media_id=media.id, ttl_seconds=600
    )
    assert partner_url is not None

    # 5. Fremder Benutzer (kein Chat-Mitglied) wird strikt abgewiesen (403 Forbidden)
    stranger = AuthService.create_user(db, "stranger", "stranger@test.de", "StrangerPass123!")
    with pytest.raises(HTTPException) as exc_info:
        ChatMediaService.generate_signed_url(
            db, user=stranger, media_id=media.id, ttl_seconds=600
        )
    assert exc_info.value.status_code == 403
    assert "Keine Chat-Mitgliedschaft" in exc_info.value.detail

    # 6. Partner kann Medium ueber signierte URL abrufen
    import urllib.parse
    parsed = urllib.parse.urlparse(partner_url)
    qs = urllib.parse.parse_qs(parsed.query)
    token = qs["token"][0]
    expires = int(qs["expires"][0])

    retrieved = ChatMediaService.get_media_by_signed_url(
        db,
        media_id=media.id,
        token=token,
        expires=expires,
        user_id=regular_user.id,
    )
    assert retrieved.id == media.id
    assert retrieved.ciphertext_blob == ciphertext

    # 7. Ablaufzeit pruefen: Abgelaufener Token wird verweigert
    past_timestamp = int(time.time()) - 100
    with pytest.raises(HTTPException) as exc_exp:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token=token,
            expires=past_timestamp,
            user_id=regular_user.id,
        )
    assert exc_exp.value.status_code == 403
    assert "abgelaufen" in exc_exp.value.detail

    # 8. Manipulierter Token (falscher HMAC) wird verweigert
    with pytest.raises(HTTPException) as exc_tamper:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token="invalid_forged_token_value",
            expires=expires,
            user_id=regular_user.id,
        )
    assert exc_tamper.value.status_code == 403
    assert "Ungueltige Signatur" in exc_tamper.value.detail

    # 9. Token fuer User A kann nicht von User B eingeloest werden
    with pytest.raises(HTTPException) as exc_cross:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token=token,  # ausgestellt fuer regular_user
            expires=expires,
            user_id=stranger.id,
        )
    assert exc_cross.value.status_code == 403


def test_group_chat_media_membership_enforcement(db: Session, owner_user: User, regular_user: User):
    """Stellt sicher, dass bei Gruppenmedien ausschliesslich aktuelle Gruppenmitglieder Zugriff haben."""
    # 1. Erstelle Gruppe mit Owner und regular_user
    group = SocialService.create_group(
        db,
        user=owner_user,
        name="Security Research Group",
        description="Streng vertraulich",
    )
    SocialService.join_group_by_invite_code(db, user=regular_user, invite_code=group.invite_code)

    # 2. Drittnutzer (Fremder)
    stranger = AuthService.create_user(db, "intruder", "intruder@test.de", "IntruderPass123!")

    # 3. Upload fuer Gruppe
    blob = "sv-blob-v1:AES-GCM-256:group_payload_data_here"
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=regular_user,
        blind_mailbox_id=f"group-{group.id}",
        ciphertext_blob=blob,
        file_name="exploit_analysis.pdf",
        group_id=group.id,
    )
    assert media.group_id == group.id

    # 4. Fremder versucht Upload in fremde Gruppe -> 403
    with pytest.raises(HTTPException) as exc_up:
        ChatMediaService.upload_encrypted_media(
            db,
            uploader=stranger,
            blind_mailbox_id=f"group-{group.id}",
            ciphertext_blob=blob,
            file_name="evil.pdf",
            group_id=group.id,
        )
    assert exc_up.value.status_code == 403

    # 5. Fremder versucht signierte URL zu generieren -> 403
    with pytest.raises(HTTPException) as exc_gen:
        ChatMediaService.generate_signed_url(
            db, user=stranger, media_id=media.id
        )
    assert exc_gen.value.status_code == 403

    # 6. Gruppenmitglied generiert signierte URL
    signed_url, _ = ChatMediaService.generate_signed_url(
        db, user=regular_user, media_id=media.id
    )
    import urllib.parse
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(signed_url).query)

    # 7. Wenn Mitglied aus der Gruppe austritt, verliert es sofort den Zugriff
    SocialService.leave_group(db, user=regular_user, group_id=group.id)

    # Erneuter Abrufversuch mit dem zuvor generierten Token schlaegt fehl (403),
    # da ChatMediaService bei jedem Download die Mitgliedschaft erneut prueft!
    with pytest.raises(HTTPException) as exc_revoked:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token=qs["token"][0],
            expires=int(qs["expires"][0]),
            user_id=regular_user.id,
        )
    assert exc_revoked.value.status_code == 403
    assert "Keine Chat-Mitgliedschaft" in exc_revoked.value.detail


def test_sanitize_attachment_filename():
    """Prueft Bereinigung von Dateinamen gegen Path Traversal, CRLF und Injektionen."""
    # 1. Normaler Dateiname
    assert sanitize_attachment_filename("report.pdf") == "report.pdf"

    # 2. Path Traversal
    assert sanitize_attachment_filename("../../etc/passwd") == "passwd"
    assert sanitize_attachment_filename("C:\\Windows\\System32\\calc.exe") == "calc.exe"

    # 3. CRLF Injection
    assert "\r" not in sanitize_attachment_filename("malicious\r\nSet-Cookie: evil=1.pdf")
    assert "\n" not in sanitize_attachment_filename("malicious\r\nSet-Cookie: evil=1.pdf")

    # 4. Quotes / Header Parameter Breakout
    assert '"' not in sanitize_attachment_filename('evil"file.exe')
    assert "'" not in sanitize_attachment_filename("evil'file.exe")

    # 5. Windows Reservierte Namen (CON, PRN, AUX, NUL)
    assert sanitize_attachment_filename("CON.txt") == "msm_CON.txt"
    assert sanitize_attachment_filename("prn.pdf") == "msm_prn.pdf"

    # 6. Leer oder None
    assert sanitize_attachment_filename(None) == "attachment.bin"
    assert sanitize_attachment_filename("   ") == "attachment.bin"


def test_chat_media_blocked_user_denial(db: Session, owner_user: User, regular_user: User):
    """Stellt sicher, dass blockierte Benutzer weder Medien hochladen noch signierte URLs anfordern koennen."""
    # 1. Erstelle Chat
    friend_req = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, friend_req["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)

    # 2. Upload vor Blockierung
    ciphertext = "sv-blob-v1:AES-GCM:iv=12345678:tag=87654321:ciphertext=EncryptedPayloadBytesXYZ"
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=owner_user,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_blob=ciphertext,
        file_name="geheim.pdf",
    )

    # 3. Blockiere Benutzer (owner_user blockiert regular_user)
    SocialService.block_user(db, owner_user.id, regular_user.id)

    # 4. Blockierter Benutzer versucht signierte URL zu erzeugen -> 403
    with pytest.raises(HTTPException) as exc_info:
        ChatMediaService.generate_signed_url(
            db, user=regular_user, media_id=media.id
        )
    assert exc_info.value.status_code == 403
    assert "blockiert" in exc_info.value.detail.lower()

    # 5. Blockierter Benutzer versucht neuen Medienupload an den Blockierenden -> 403
    with pytest.raises(HTTPException) as exc_up:
        ChatMediaService.upload_encrypted_media(
            db,
            uploader=regular_user,
            blind_mailbox_id=chat.blind_mailbox_id,
            ciphertext_blob=ciphertext,
            file_name="spam.pdf",
        )
    assert exc_up.value.status_code == 403


def test_chat_media_expired_media_denial(db: Session, owner_user: User, regular_user: User):
    """Prueft, dass abgelaufene Medien (Retention) mit 410 abgewiesen werden."""
    from datetime import timedelta
    friend_req = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, friend_req["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)

    ciphertext = "sv-blob-v1:AES-GCM:iv=12345678:tag=87654321:ciphertext=EncryptedPayloadBytesXYZ"
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=owner_user,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_blob=ciphertext,
        file_name="veraltet.pdf",
    )

    # Erzeuge gueltigen signierten URL Token
    signed_url, _ = ChatMediaService.generate_signed_url(db, user=owner_user, media_id=media.id)
    import urllib.parse
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(signed_url).query)

    # Setze media.expires_at manuell in die Vergangenheit
    media.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    # Abruf muss mit 410 abgewiesen werden
    with pytest.raises(HTTPException) as exc_exp:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token=qs["token"][0],
            expires=int(qs["expires"][0]),
            user_id=owner_user.id,
        )
    assert exc_exp.value.status_code == 410
    assert "abgelaufen" in exc_exp.value.detail


# ---------------------------------------------------------------------------
# 7. Erweiterte Archiv- & TAR-Bomb Pruefungen
# ---------------------------------------------------------------------------

def test_additional_archive_formats_and_tar_bomb_detection():
    """Prueft Erkennung von 7z, RAR, BZ2, XZ und TAR sowie Schutz vor bösartigen TAR-Archiven."""
    import tarfile

    # 1. Magic Bytes Erkennung
    assert detect_mime_from_magic_bytes(b"7z\xbc\xaf\x27\x1c\x00\x00") == "application/x-7z-compressed"
    assert detect_mime_from_magic_bytes(b"Rar!\x1a\x07\x00\x00") == "application/vnd.rar"
    assert detect_mime_from_magic_bytes(b"BZh91AY&SY\x00") == "application/x-bzip2"
    assert detect_mime_from_magic_bytes(b"\xfd7zXZ\x00\x00") == "application/x-xz"

    # TAR Header (ustar bei Offset 257)
    tar_header = b"\x00" * 257 + b"ustar" + b"\x00" * 250
    assert detect_mime_from_magic_bytes(tar_header) == "application/x-tar"

    # 2. Gueltiges kleines TAR-Archiv
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        info = tarfile.TarInfo(name="readme.txt")
        data = b"Clean TAR content"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    clean_tar = bio.getvalue()
    inspect_archive_for_zip_bomb(clean_tar)  # Darf keine Exception werfen

    # 3. Path Traversal im TAR (Tar-Slip)
    slip_bio = io.BytesIO()
    with tarfile.open(fileobj=slip_bio, mode="w") as tf:
        info = tarfile.TarInfo(name="../../etc/shadow")
        data = b"root:*:0:0"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    slip_tar = slip_bio.getvalue()
    with pytest.raises(ZipBombDetectedError, match="Gefaehrlicher Pfad"):
        inspect_archive_for_zip_bomb(slip_tar)

    # 4. Verschachteltes Archiv im TAR
    nested_bio = io.BytesIO()
    with tarfile.open(fileobj=nested_bio, mode="w") as tf:
        info = tarfile.TarInfo(name="secret/payload.zip")
        data = b"PK\x03\x04"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    nested_tar = nested_bio.getvalue()
    with pytest.raises(ZipBombDetectedError, match="Verschachtelte Archive"):
        inspect_archive_for_zip_bomb(nested_tar)


# ---------------------------------------------------------------------------
# 8. Erweiterte SVG-CSS und Style-XSS Schutzpruefung
# ---------------------------------------------------------------------------

def test_svg_css_and_style_xss_protection():
    """Stellt sicher, dass SVG-basierte XSS-Angriffe via CSS (<style> oder Attribut) blockiert werden."""
    # 1. <style> mit javascript: URL
    evil_style_js = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>circle { background: url("javascript:alert(1)"); }</style>'
        '<circle cx="5" cy="5" r="4"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_style_js)

    # 2. <style> mit @import
    evil_style_import = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>@import url("https://evil.com/xss.css");</style>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_style_import)

    # 3. <style> mit expression()
    evil_style_expr = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>div { width: expression(alert(1)); }</style>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_style_expr)

    # 4. style-Attribut mit javascript:
    evil_attr_style = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<rect width="10" height="10" style="fill: url(\'javascript:alert(1)\')"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_attr_style)

    # 5. style-Attribut mit expression()
    evil_attr_expr = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<rect width="10" height="10" style="width: expression(alert(1))"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_attr_expr)


# ---------------------------------------------------------------------------
# 9. Signierte URLs Token-Fälschung & Integrität
# ---------------------------------------------------------------------------

def test_signed_url_tampering_protection(db: Session, owner_user: User, regular_user: User):
    """Prueft Schutz vor Parameter-Manipulation in signierten URLs."""
    friend_req = SocialService.send_friend_request(db, owner_user.id, regular_user.username)
    SocialService.accept_friend_request(db, regular_user.id, friend_req["id"])
    chat = SocialService.ensure_direct_chat(db, owner_user.id, regular_user.id)

    ciphertext = "sv-blob-v1:AES-GCM:iv=111:tag=222:ciphertext=GueltigeE2EEPayloadDaten"
    media = ChatMediaService.upload_encrypted_media(
        db,
        uploader=owner_user,
        blind_mailbox_id=chat.blind_mailbox_id,
        ciphertext_blob=ciphertext,
        file_name="tamper_test.pdf",
    )

    signed_url, _ = ChatMediaService.generate_signed_url(db, user=owner_user, media_id=media.id)
    import urllib.parse
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(signed_url).query)
    token = qs["token"][0]
    expires = int(qs["expires"][0])

    # 1. Modifizierte expires-Zeit mit originalem Token -> 403
    with pytest.raises(HTTPException) as exc_exp:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id=media.id,
            token=token,
            expires=expires + 1000,  # manipuliert
            user_id=owner_user.id,
        )
    assert exc_exp.value.status_code == 403

    # 2. Modifizierte media_id mit originalem Token -> 403
    with pytest.raises(HTTPException) as exc_media:
        ChatMediaService.get_media_by_signed_url(
            db,
            media_id="fake-media-id-12345",
            token=token,
            expires=expires,
            user_id=owner_user.id,
        )
    assert exc_media.value.status_code == 403


# ---------------------------------------------------------------------------
# 10. Erweiterte Edge-Case & Exploit Tests
# ---------------------------------------------------------------------------

def test_gzip_bomb_isize_modulo_overflow_detection():
    """Stellt sicher, dass GZIP-Bomben mit ISIZE Modulo 2^32 oder gefaelschter Groesse zuverlaessig erkannt werden."""
    import gzip
    import struct

    # Erzeuge ein GZIP mit vielen Nullen (dekomprimiert > 50 MB)
    bio = io.BytesIO()
    with gzip.GzipFile(fileobj=bio, mode="wb", compresslevel=9) as gz:
        gz.write(b"\x00" * (55 * 1024 * 1024))
    raw_gz = bytearray(bio.getvalue())

    # Manipuliere ISIZE am Ende auf 10 Bytes (als ob 2^32 overflow vorlag oder manipuliert wurde)
    raw_gz[-4:] = struct.pack("<I", 10)

    # Muss durch Stream-Dekompression dennoch als ZipBomb erkannt werden!
    with pytest.raises(ZipBombDetectedError):
        inspect_archive_for_zip_bomb(bytes(raw_gz))


def test_zip_bomb_header_compress_size_spoofing():
    """Prueft, dass manipulierte compress_size-Header in ZIP-Dateien die Bomben-Erkennung nicht umgehen koennen."""
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.bin", b"\x00" * (3 * 1024 * 1024))
    raw_zip = bytearray(bio.getvalue())

    # Die Erkennung berechnet ratio = total_uncompressed / len(data)
    # len(data) ist nur wenige Kilobytes, entpackt sind es 3MB -> ratio > 25:1
    with pytest.raises(ZipBombDetectedError):
        inspect_archive_for_zip_bomb(bytes(raw_zip))


def test_polyglot_jpeg_zip_detection():
    """Erkennt polyglote Archive, die mit gueltigen Bild-Headern getarnt sind."""
    # Erzeuge gueltigen ZIP mit einer Zip-Slip Datei
    zip_bio = io.BytesIO()
    with zipfile.ZipFile(zip_bio, "w") as zf:
        zf.writestr("../../etc/shadow", "root:x:0:0")
    zip_bytes = zip_bio.getvalue()

    # Prepend JPEG Magic Bytes
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    polyglot = jpeg_header + zip_bytes

    # inspect_archive_for_zip_bomb muss das eingebettete ZIP erkennen und Zip-Slip abweisen!
    with pytest.raises(ZipBombDetectedError, match="Zip-Slip"):
        inspect_archive_for_zip_bomb(polyglot)


def test_declared_filename_executable_extension_blocking():
    """Stellt sicher, dass deklarierte ausfuehrbare Dateinamen (.exe, .bat, etc.) blockiert werden."""
    clean_bytes = b"Just plain text data that does not look like an executable"
    with pytest.raises(ExecutableBlockedError):
        validate_file_mime_and_magic(
            clean_bytes,
            declared_filename="run_me.exe",
        )

    with pytest.raises(ExecutableBlockedError):
        validate_file_mime_and_magic(
            clean_bytes,
            declared_filename="install.bat",
        )

    with pytest.raises(ExecutableBlockedError):
        validate_file_mime_and_magic(
            clean_bytes,
            declared_filename="script.ps1",
        )


def test_bom_prefixed_executable_detection():
    """Stellt sicher, dass UTF-BOM vor Executable- oder Skript-Signaturen gestrippt und geblockt wird."""
    bom_exe = b"\xef\xbb\xbfMZ\x90\x00\x03\x00\x00\x00"
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(bom_exe)

    bom_shebang = b"\xef\xbb\xbf#!/bin/bash\necho pwned"
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(bom_shebang)


def test_batch_and_php_script_blocking():
    """Blockiert Windows Batch- und PHP-Skripte."""
    batch_data = b"@echo off\ncalc.exe"
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(batch_data)

    php_data = b"<?php phpinfo(); ?>"
    with pytest.raises(ExecutableBlockedError):
        detect_mime_from_magic_bytes(php_data)


def test_svg_css_comments_and_escape_obfuscation():
    """Verhindert, dass CSS-Kommentare oder Backslash-Escapes in SVG-Styles XSS-Erkennung umgehen."""
    # 1. CSS-Kommentare in javascript:
    evil_comment = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>circle { background: url("jav/*secret*/ascript:alert(1)"); }</style>'
        '<circle cx="5" cy="5" r="4"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_comment)

    # 2. CSS-Kommentare in @import
    evil_import = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>@/*x*/import url("https://evil.com/x.css");</style>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_import)

    # 3. CSS Backslash Escape
    evil_escape = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<style>circle { width: exp\\72 ession(alert(1)); }</style>'
        '<circle cx="5" cy="5" r="4"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_escape)


def test_svg_use_tag_blocking():
    """Stellt sicher, dass gefaehrliche <use> Tags abgewiesen werden."""
    evil_use = (
        '<svg xmlns="http://www.w3.org/2000/svg">'
        '<use href="https://evil.com/exploit.svg#icon"/>'
        '<circle cx="5" cy="5" r="4"/>'
        '</svg>'
    )
    with pytest.raises(SvgXssDetectedError):
        sanitize_svg_content(evil_use)


def test_filename_ntfs_alternate_data_stream_sanitization():
    """Stellt sicher, dass Windows NTFS Alternate Data Streams (ADS) durch Bereinigung neutralisiert werden."""
    sanitized = sanitize_attachment_filename("malicious.exe:hidden_stream")
    assert ":" not in sanitized
    assert sanitized == "malicious.exe_hidden_stream"


def test_upload_unauthorized_mailbox_rejection(db: Session, owner_user: User):
    """Verhindert, dass unberechtigte Blobs fuer frei erfundene Mailbox-IDs ohne Chat hochgeladen werden."""
    ciphertext = "sv-blob-v1:AES-GCM:iv=111:tag=222:ciphertext=ValidE2EEPayload"
    with pytest.raises(HTTPException) as exc_info:
        ChatMediaService.upload_encrypted_media(
            db,
            uploader=owner_user,
            blind_mailbox_id="completely-bogus-mailbox-id-without-chat",
            ciphertext_blob=ciphertext,
            file_name="unauthorized.pdf",
        )
    assert exc_info.value.status_code == 403
    assert "Keine gueltige Chat-Mitgliedschaft" in exc_info.value.detail


