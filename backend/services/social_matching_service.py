from __future__ import annotations

import difflib
import json
import logging
import re
from typing import Any
from sqlalchemy.orm import Session
from models import User, TeamMember
from models.ai_memory import AiMemoryEntry
from services import ai_memory_service
from services.social_service import SocialService

logger = logging.getLogger(__name__)

_NORMALIZE_RE = re.compile(r"[_\-.:/,\s]+")

_RELATIONSHIP_TERMS = (
    "bester freund",
    "beste freundin",
    "best friend",
    "bff",
    "freund",
    "freundin",
    "kollege",
    "kollegin",
    "chef",
    "chefin",
    "boss",
    "manager",
    "spitzname",
    "nickname",
    "alias",
    "partner",
    "partnerin",
    "ehemann",
    "ehefrau",
    "mann",
    "frau",
    "bruder",
    "schwester",
    "vater",
    "mutter",
    "papa",
    "mama",
    "sohn",
    "tochter",
)

_PREFIXES_TO_STRIP = (
    "an meinen ",
    "an meine ",
    "an meinem ",
    "an meiner ",
    "an mein ",
    "an den ",
    "an die ",
    "an das ",
    "an dem ",
    "an einen ",
    "an eine ",
    "an einem ",
    "an ",
    "für meinen ",
    "für meine ",
    "für mein ",
    "für den ",
    "für die ",
    "für das ",
    "für ",
    "mit meinem ",
    "mit meiner ",
    "mit mein ",
    "mit dem ",
    "mit der ",
    "mit ",
    "meinen ",
    "meine ",
    "meinem ",
    "meiner ",
    "meines ",
    "mein ",
    "den ",
    "die ",
    "das ",
    "dem ",
    "der ",
    "des ",
    "einen ",
    "eine ",
    "einem ",
    "einer ",
    "ein ",
)


class SocialMatchingService:
    """Löst Benutzernamen anhand von Memory-Einträgen (Beziehungs-Aliase, Spitznamen)
    und fehlertoleranter Fuzzy-Suche über Kontakte semantisch auf."""

    @classmethod
    def normalize_term(cls, text: str) -> str:
        """Normalisiert einen Begriff (Kleinschreibung, Wortabstände, Stemming von Floskeln)."""
        s = text.strip().lower()
        s = _NORMALIZE_RE.sub(" ", s).strip()
        # Floskeln/Präfixe entfernen
        s = cls.clean_alias_query(s)
        # Grammatikalische Normalisierung für Beziehungsbegriffe
        if s.startswith("besten freund") or s.startswith("bester freund"):
            s = "bester freund"
        elif s.startswith("beste freundin") or s.startswith("besten freundin"):
            s = "beste freundin"
        elif s == "kollegen":
            s = "kollege"
        return s

    @classmethod
    def clean_alias_query(cls, text: str) -> str:
        """Entfernt Floskeln wie 'meinen besten', 'meine', 'an meinen' etc."""
        s = text.strip().lower()
        # Wiederholt Präfixe abziehen, falls kombiniert (z. B. "an" + "meinen")
        changed = True
        while changed:
            changed = False
            for p in _PREFIXES_TO_STRIP:
                if s.startswith(p):
                    s = s[len(p):].strip()
                    changed = True
                    break
        return s

    @classmethod
    def _is_relationship_term(cls, text: str) -> bool:
        t = text.strip().lower()
        return any(rel in t for rel in _RELATIONSHIP_TERMS)

    @classmethod
    def get_user_memory_aliases(cls, db: Session, user: User) -> list[dict[str, Any]]:
        """Liest die Memory-Einträge des Benutzers aus und extrahiert Beziehungs-Aliase
        und Spitznamen (z. B. 'bester Freund = Raik', 'Spitzname: Ali = alice_wonderland')."""
        aliases: list[dict[str, Any]] = []
        try:
            rows = db.query(AiMemoryEntry).filter(
                AiMemoryEntry.owner_user_id == user.id,
                AiMemoryEntry.scope.in_(("user", "server")),
            ).all()
            if not rows:
                return aliases

            decrypted = ai_memory_service._entschluesseln_lesbare(rows)
            for row, val_text in decrypted:
                key_clean = row.key.lower().replace("_", " ").replace("-", " ").replace(".", " ").strip()
                val_clean = val_text.strip()

                # A. Schlüssel-basierte Zuordnung (z. B. key="bester_freund", val="Raik")
                if cls._is_relationship_term(key_clean):
                    aliases.append({
                        "alias": key_clean,
                        "raw_target": val_clean,
                        "source": "key_to_value",
                    })

                # B. Textmuster im Wert analysieren
                for line in val_clean.splitlines():
                    line = line.strip()
                    if not line:
                        continue

                    # Spezifische Spitznamen-Muster:
                    # "Ali ist der Spitzname von alice_wonderland"
                    m_nick1 = re.search(r"^(.*?)\s+ist\s+(?:der\s+)?spitzname\s+von\s+(.*?)$", line, re.IGNORECASE)
                    if m_nick1:
                        nick, target = m_nick1.group(1).strip(), m_nick1.group(2).strip()
                        aliases.append({"alias": nick.lower(), "raw_target": target, "source": "nick_pattern"})
                        continue

                    # "Spitzname von alice_wonderland ist Ali" / "Der Spitzname von alice_wonderland ist Ali"
                    m_nick2 = re.search(r"^(?:der\s+)?spitzname\s+von\s+(.*?)\s+ist\s+(.*?)$", line, re.IGNORECASE)
                    if m_nick2:
                        target, nick = m_nick2.group(1).strip(), m_nick2.group(2).strip()
                        aliases.append({"alias": nick.lower(), "raw_target": target, "source": "nick_pattern"})
                        continue

                    # Zuweisungsmuster (=, :, ->, ist, heißt)
                    parts = re.split(r"\s*(?:=|:|->|ist|heißt)\s*", line, maxsplit=1)
                    if len(parts) == 2:
                        left = parts[0].strip()
                        right = parts[1].strip()
                        if left and right:
                            left_is_rel = cls._is_relationship_term(left)
                            right_is_rel = cls._is_relationship_term(right)

                            if left_is_rel and not right_is_rel:
                                # "Mein bester Freund ist Raik" -> alias: "mein bester freund", target: "Raik"
                                aliases.append({"alias": left.lower(), "raw_target": right, "source": "line_split"})
                            elif right_is_rel and not left_is_rel:
                                # "Raik ist mein bester Freund" -> alias: "mein bester freund", target: "Raik"
                                aliases.append({"alias": right.lower(), "raw_target": left, "source": "line_split"})
                            else:
                                # Symmetrisch (z. B. "Ali = alice_wonderland")
                                aliases.append({"alias": left.lower(), "raw_target": right, "source": "line_split"})
                                aliases.append({"alias": right.lower(), "raw_target": left, "source": "line_split_rev"})

                    # Klammer-Muster: "Raik (bester Freund)" oder "bester Freund (Raik)"
                    m = re.search(r"^([^\(]+)\s*\(([^\)]+)\)$", line)
                    if m:
                        a, b = m.group(1).strip(), m.group(2).strip()
                        if cls._is_relationship_term(b):
                            aliases.append({"alias": b.lower(), "raw_target": a, "source": "parens"})
                        elif cls._is_relationship_term(a):
                            aliases.append({"alias": a.lower(), "raw_target": b, "source": "parens"})
                        else:
                            aliases.append({"alias": b.lower(), "raw_target": a, "source": "parens"})
                            aliases.append({"alias": a.lower(), "raw_target": b, "source": "parens_rev"})

                    # JSON Format
                    if line.startswith("{") and line.endswith("}"):
                        try:
                            data = json.loads(line)
                            if isinstance(data, dict):
                                for k, v in data.items():
                                    if isinstance(v, str):
                                        aliases.append({"alias": str(k).lower(), "raw_target": v, "source": "json"})
                        except Exception:
                            pass

        except Exception as e:
            logger.warning("Fehler beim Extrahieren der Memory-Aliase: %s", e)

        return aliases

    @classmethod
    def _resolve_target_to_user(
        cls,
        db: Session,
        user: User,
        raw_target: str,
        *,
        friend_only: bool = False,
    ) -> User | None:
        """Löst ein Namensziel (z. B. 'Raik', 'raik_gamer', '@alice_wonderland') in einen User auf."""
        target_clean = raw_target.strip().strip("@.,;:!?\"'()[]{}<>-=_")
        if not target_clean:
            return None

        # 1. Direkter Lookup mit gesäubertem Ziel
        direct = db.query(User).filter(User.username.ilike(target_clean), User.is_active.is_(True)).first()
        if direct and direct.id != user.id:
            if not friend_only or SocialService.is_confirmed_friend(db, user.id, direct.id):
                return direct

        # 2. Token-weiser Lookup (z. B. wenn raw_target 'Raik (raik_gamer)' oder 'Raik.' ist)
        raw_tokens = re.findall(r"[A-Za-z0-9_.-]+", raw_target)
        tokens: list[str] = []
        for t in raw_tokens:
            c = t.strip("@.,;:!?\"'()[]{}<>-=_")
            if c and c.lower() not in ("der", "die", "das", "ein", "eine", "ist", "heißt", "von", "den", "dem"):
                tokens.append(c)

        for tok in tokens:
            found = db.query(User).filter(User.username.ilike(tok), User.is_active.is_(True)).first()
            if found and found.id != user.id:
                if not friend_only or SocialService.is_confirmed_friend(db, user.id, found.id):
                    return found

        # 3. Wenn exakter Username nicht passt (z. B. Ziel 'Raik', aber Username 'raik_gamer'):
        # Kandidaten des Benutzers nach Teilstring- oder Fuzzy-Übereinstimmung durchsuchen
        candidates = cls._get_candidate_users(db, user, friend_only=friend_only)
        candidate_words = [target_clean.lower()] + [t.lower() for t in tokens]

        # A. Teilstring-Suche (z. B. 'raik' in 'raik_gamer')
        for word in candidate_words:
            if len(word) >= 3:
                for cand in candidates:
                    c_name = cand.username.lower()
                    if word == c_name or word in c_name or (len(c_name) >= 3 and c_name in word):
                        return cand

        # B. Fuzzy-Suche über Kandidaten
        best_cand: User | None = None
        best_ratio = 0.0
        for word in candidate_words:
            if len(word) >= 2:
                for cand in candidates:
                    r = difflib.SequenceMatcher(None, word, cand.username.lower()).ratio()
                    if r > best_ratio:
                        best_ratio = r
                        best_cand = cand

        if best_cand and best_ratio >= 0.65:
            return best_cand

        return None

    @classmethod
    def resolve_contact(
        cls,
        db: Session,
        user: User,
        raw_name: str,
        *,
        friend_only: bool = False,
    ) -> User | None:
        """Löst einen Namen, Alias oder Spitznamen in ein konkretes User-Objekt auf.
        
        1. Direkter Abgleich mit Benutzernamen (wenn bereits bestätigter Freund)
        2. Memory-Abgleich nach Beziehungs-Aliasen (z. B. 'bester Freund = Raik') und Spitznamen
        3. Semantische Suche via Memory-Embeddings
        4. Fehlertolerante Fuzzy-Suche über Kontakte (Tippfehler)
        5. Direkter Abgleich mit beliebigem aktiven Benutzer
        """
        raw_clean = raw_name.strip()
        if not raw_clean:
            return None

        # 1. Direkter Treffer mit bestätigtem Freund
        direct = db.query(User).filter(User.username.ilike(raw_clean), User.is_active.is_(True)).first()
        if direct and direct.id != user.id and SocialService.is_confirmed_friend(db, user.id, direct.id):
            return direct

        # 2. Memory-basierter Abgleich nach Aliasen & Spitznamen
        q_norm = cls.normalize_term(raw_clean)
        q_clean = cls.clean_alias_query(q_norm)
        aliases = cls.get_user_memory_aliases(db, user)

        for entry in aliases:
            alias_name = cls.normalize_term(entry["alias"])
            matched = False

            if q_norm and alias_name:
                if q_norm == alias_name or q_clean == alias_name:
                    matched = True
                elif q_norm in alias_name or alias_name in q_norm:
                    matched = True
                elif q_clean and (q_clean in alias_name or alias_name in q_clean):
                    matched = True
                elif difflib.SequenceMatcher(None, q_norm, alias_name).ratio() >= 0.75:
                    matched = True

            if matched:
                resolved = cls._resolve_target_to_user(db, user, entry["raw_target"], friend_only=friend_only)
                if resolved:
                    return resolved

        # 3. Semantische Suche im Gedächtnis
        try:
            hits = ai_memory_service.search_entries(db, user, raw_clean, limit=5)
            for row, val_text, _score in hits:
                combined_text = f"{row.key} {val_text}"
                resolved = cls._resolve_target_to_user(db, user, combined_text, friend_only=friend_only)
                if resolved:
                    return resolved
        except Exception:
            pass

        # 4. Fuzzy-Suche über Kontakte (Schreibfehler / Tippfehler-Toleranz)
        candidates = cls._get_candidate_users(db, user, friend_only=friend_only)
        best_candidate: User | None = None
        best_ratio = 0.0

        for cand in candidates:
            c_name = cand.username.lower()
            ratio = difflib.SequenceMatcher(None, q_clean, c_name).ratio()
            ratio_raw = difflib.SequenceMatcher(None, q_norm, c_name).ratio()
            max_r = max(ratio, ratio_raw)

            # Substring-Bonus nur wenn sinnvoll (ab 3 Zeichen)
            if len(q_clean) >= 3 and len(c_name) >= 3:
                if q_clean in c_name or c_name in q_clean:
                    max_r = max(max_r, 0.75)

            if max_r > best_ratio:
                best_ratio = max_r
                best_candidate = cand

        if best_candidate and best_ratio >= 0.60:
            return best_candidate

        # 5. Direkter Treffer mit beliebigem aktiven Systemnutzer (wenn nicht friend_only)
        if direct and direct.id != user.id and not friend_only:
            return direct

        return None

    @classmethod
    def _get_candidate_users(cls, db: Session, user: User, *, friend_only: bool = False) -> list[User]:
        """Sammelt in Frage kommende Kontakt-Benutzer für den Aufrufer."""
        seen_ids: set[int] = {user.id}
        result: list[User] = []

        # 1. Bestätigte Freunde
        friends = SocialService.get_friends(db, user.id)
        for f in friends:
            uid = int(f.get("user_id") or f.get("id") or 0)
            if uid and uid not in seen_ids:
                u = db.query(User).filter(User.id == uid, User.is_active.is_(True)).first()
                if u:
                    seen_ids.add(uid)
                    result.append(u)

        if friend_only:
            return result

        # 2. Teammitglieder
        user_team_ids = [tm.team_id for tm in db.query(TeamMember.team_id).filter(TeamMember.user_id == user.id).all()]
        if user_team_ids:
            colleagues = (
                db.query(User)
                .join(TeamMember, TeamMember.user_id == User.id)
                .filter(TeamMember.team_id.in_(user_team_ids), User.id != user.id, User.is_active.is_(True))
                .all()
            )
            for c in colleagues:
                if c.id not in seen_ids:
                    seen_ids.add(c.id)
                    result.append(c)

        # 3. Öffentliche & aktive Benutzer
        public_users = (
            db.query(User)
            .filter(User.id != user.id, User.is_active.is_(True))
            .limit(50)
            .all()
        )
        for pu in public_users:
            if pu.id not in seen_ids:
                seen_ids.add(pu.id)
                result.append(pu)

        return result

    @classmethod
    def search_contacts(cls, db: Session, user: User, query: str) -> list[dict[str, Any]]:
        """Sucht im Messenger nach Kontakten mit semantischer Alias- und Fuzzy-Erkennung."""
        q_raw = query.strip()
        if not q_raw:
            return []

        q_norm = cls.normalize_term(q_raw)
        q_clean = cls.clean_alias_query(q_norm)

        results: list[dict[str, Any]] = []
        seen_ids: set[int] = {user.id}

        # 1. Prüfe zuerst Memory auf Beziehungs-Aliase / Spitznamen
        resolved_from_alias = cls.resolve_contact(db, user, q_raw, friend_only=False)
        if resolved_from_alias and resolved_from_alias.id not in seen_ids:
            seen_ids.add(resolved_from_alias.id)
            is_fr = SocialService.is_confirmed_friend(db, user.id, resolved_from_alias.id)
            results.append({
                "user_id": resolved_from_alias.id,
                "username": resolved_from_alias.username,
                "relationship": "friend" if is_fr else "contact",
                "matched_by": "memory_alias",
            })

        # 2. Durchsuche Kontakte (Freunde, Team, Öffentlich) mit Exakt- und Fuzzy-Suche
        candidates = cls._get_candidate_users(db, user, friend_only=False)
        scored_candidates: list[tuple[float, User, str]] = []

        for cand in candidates:
            if cand.id in seen_ids:
                continue

            c_name = cand.username.lower()
            rel = "friend" if SocialService.is_confirmed_friend(db, user.id, cand.id) else "user"

            # Exakter Teilstring-Treffer
            if (len(q_clean) >= 3 and q_clean in c_name) or (len(q_norm) >= 3 and q_norm in c_name):
                scored_candidates.append((1.0, cand, rel))
                continue

            # Fuzzy-Ähnlichkeit
            ratio = max(
                difflib.SequenceMatcher(None, q_clean, c_name).ratio(),
                difflib.SequenceMatcher(None, q_norm, c_name).ratio(),
            )
            if ratio >= 0.60:
                scored_candidates.append((ratio, cand, rel))

        # Nach Relevanz sortieren
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        for score, cand, rel in scored_candidates:
            if cand.id not in seen_ids:
                seen_ids.add(cand.id)
                results.append({
                    "user_id": cand.id,
                    "username": cand.username,
                    "relationship": rel,
                    "match_score": round(score, 2),
                })

        return results
