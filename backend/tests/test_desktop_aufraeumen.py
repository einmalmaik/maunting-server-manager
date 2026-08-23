"""Aufraeumen ausserhalb der Sandbox — wer entscheidet, und wer nicht.

Der Betreiber hat die Regel am 23.08.2026 woertlich diktiert: *"vieles
funktioniert nur im autonomen Modus, ansonsten musst du fuer alles eine
Bestaetigung haben. Nur wenn der autonome Modus an ist, dann wird keine
Bestaetigung gefragt."* Diese Datei haelt fest, dass die Antwort auf "muss
gefragt werden?" **im Panel** entsteht und nirgends sonst:

1. Das Modell kann sie nicht setzen. Schickt es `autonom` oder `systembereich`
   in den Werkzeugargumenten mit, fliegen sie raus — sonst waere es eine
   Selbstermaechtigung, die genau einmal funktionieren muesste.
2. Ohne Autonomiefreigabe ist die Antwort `False`, mit Freigabe `True`.
3. Der Systembereich kommt aus dem Konto, nicht aus dem Aufruf.
4. Ein Auftrag, der auf einen Menschen warten kann, bekommt die lange Frist —
   und seit dem Zusammenlegen von `desktop_takeover_control` in
   `desktop_steuern` haengt das an einem **Argument**, nicht am Namen.
"""

from types import SimpleNamespace

from sqlalchemy.orm import Session

from models import Role, RolePermission, User
from services import ai_autonomy_service, desktop_job_service
from services.ai_stream_service import _desktop_argumente
from services.role_service import set_user_roles


def _aufruf(name: str, argumente: dict) -> SimpleNamespace:
    """Ein Werkzeugaufruf, wie ihn `StreamUsage.tool_calls` liefert."""
    return SimpleNamespace(id="ruf-1", name=name, arguments=argumente)


def _mit_autonomie(db: Session, user: User) -> None:
    role = Role(name="autonom-desktop", description=None, is_system=False)
    db.add(role)
    db.flush()
    for recht in ("ai.desktop.use", "ai.autonomous.use"):
        db.add(RolePermission(role_id=role.id, permission_key=recht))
    db.commit()
    set_user_roles(db, user, [role.id])
    ai_autonomy_service.set_grant(
        db, user=user, server_id=None, enabled=True,
        max_actions_per_hour=10, granted_by=user.id,
    )
    db.commit()


class TestDasModellSetztSichNichtSelbstFrei:
    def test_mitgeschicktes_autonom_wird_verworfen(self, db: Session, regular_user: User):
        """Der teuerste Fall: das Modell behauptet, es duerfe ohne Rueckfrage.

        Ohne Autonomiefreigabe muss die Antwort `False` sein — egal, was in
        den Argumenten stand. Ein `True`, das durchrutscht, loescht Dateien
        ohne Karte.
        """
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_aufraeumen", {
                "aktion": "endgueltig",
                "grund": "Aufraeumen",
                "autonom": True,
                "systembereich": "schreiben",
            }),
        )
        assert argumente["autonom"] is False
        assert argumente["systembereich"] == "lesen"
        # Die echten Argumente bleiben unangetastet.
        assert argumente["aktion"] == "endgueltig"
        assert argumente["grund"] == "Aufraeumen"

    def test_mit_freigabe_steht_autonom_auf_wahr(self, db: Session, regular_user: User):
        _mit_autonomie(db, regular_user)
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_aufraeumen", {"aktion": "papierkorb", "grund": "x"}),
        )
        assert argumente["autonom"] is True

    def test_der_systembereich_kommt_aus_dem_konto(self, db: Session, regular_user: User):
        regular_user.ai_desktop_systembereich = "schreiben"
        db.commit()
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_aufraeumen", {
                "aktion": "papierkorb", "grund": "x", "systembereich": "aus",
            }),
        )
        assert argumente["systembereich"] == "schreiben"

    def test_ohne_benutzer_wird_nichts_geglaubt(self, db: Session):
        """Ein Lauf ohne auffindbares Konto darf nicht die grosszuegige Seite waehlen."""
        argumente = _desktop_argumente(
            db,
            user_id=9_999_999,
            call=_aufruf("desktop_aufraeumen", {"aktion": "papierkorb", "grund": "x"}),
        )
        assert argumente["autonom"] is False
        assert argumente["systembereich"] == "aus"

    def test_das_lesewerkzeug_bekommt_den_bereich_aber_kein_autonom(
        self, db: Session, regular_user: User
    ):
        """`aus` verschliesst auch den **Blick** — sonst waere es keine Stufe.

        Ohne dieses Feld haette `aus` nur bedeutet: sie darf dort alles
        sehen, nur nichts wegnehmen. Der Betreiber hat drei Stufen bestellt,
        nicht zwei. `autonom` steht trotzdem nicht dabei: `desktop_system`
        liest und fragt nie.
        """
        regular_user.ai_desktop_systembereich = "aus"
        db.commit()
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_system", {"aktion": "verzeichnis", "pfad": "C:\\Windows"}),
        )
        assert argumente["systembereich"] == "aus"
        assert "autonom" not in argumente

    def test_andere_desktop_werkzeuge_bekommen_kein_urteil(
        self, db: Session, regular_user: User
    ):
        """Dateien, Programme und Steuern brauchen die Felder nicht.

        Sie ueberall mitzuschicken waere kein Sicherheitsgewinn, aber ein
        Angebot an eine kuenftige Fassung der App, sie auch woanders zu
        beachten — und damit eine Regel an zwei Orten.
        """
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_dateien", {"aktion": "loeschen", "pfad": "a.txt"}),
        )
        assert "autonom" not in argumente
        assert "systembereich" not in argumente

    def test_auch_dort_fliegen_die_gesetzten_felder_raus(
        self, db: Session, regular_user: User
    ):
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_dateien", {"aktion": "auflisten", "autonom": True}),
        )
        assert "autonom" not in argumente


class TestLangeFrist:
    def test_die_bitte_um_die_freigabe_bekommt_zeit(self):
        assert desktop_job_service._wartet_auf_menschen(
            "desktop_steuern", {"aktion": "freigabe", "anliegen": "..."}
        )

    def test_ein_klick_bekommt_sie_nicht(self):
        """Der Preis des Zusammenlegens: die Frist haengt am Argument.

        Bekaeme jeder Klick zehn Minuten, stuende ein Lauf nach einem
        abgestuerzten Rechner entsprechend lange still.
        """
        assert not desktop_job_service._wartet_auf_menschen(
            "desktop_steuern", {"aktion": "klick", "x": 10, "y": 20}
        )

    def test_aufraeumen_bekommt_sie_immer(self):
        assert desktop_job_service._wartet_auf_menschen("desktop_aufraeumen", {})

    def test_ein_fehlendes_argument_ist_kein_absturz(self):
        assert not desktop_job_service._wartet_auf_menschen("desktop_steuern", {})
        assert not desktop_job_service._wartet_auf_menschen("desktop_system", {})


class TestEinstellung:
    def test_der_standard_ist_lesen(self, db: Session, regular_user: User):
        """Der heutige Zustand, nicht die goldene Mitte.

        `desktop_system` listet seit dem 21.08.2026 jedes Verzeichnis auf.
        Ein Standard `aus` waere eine stille Verschaerfung von etwas, das
        laeuft.
        """
        from models.user import systembereich_des_benutzers

        assert systembereich_des_benutzers(regular_user) == "lesen"

    def test_ein_unbekannter_wert_faellt_nach_unten(self, db: Session, regular_user: User):
        from models.user import systembereich_des_benutzers

        regular_user.ai_desktop_systembereich = "alles"
        assert systembereich_des_benutzers(regular_user) == "lesen"


class TestKlickenFolgtDemAutonomenModus:
    """Maus und Tastatur unter derselben Regel wie das Aufraeumen.

    Bis zum 23.08.2026 brauchte jeder Klick eine befristete Freigabe, auch bei
    eingeschaltetem autonomem Modus. Das war die halbe Regel: der Betreiber
    hat "keine Bestaetigung im autonomen Modus" fuer **alles** diktiert. Eine
    Karte je Klick waere die Alternative gewesen — ein Formular auszufuellen
    sind zwanzig Klicks, und damit zwanzig Karten.
    """

    def test_ohne_freigabe_bleibt_es_beim_fragen(self, db: Session, regular_user: User):
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_steuern", {"aktion": "klick", "x": 10, "y": 20}),
        )
        assert argumente["autonom"] is False

    def test_mit_freigabe_klickt_sie_ohne_karte(self, db: Session, regular_user: User):
        _mit_autonomie(db, regular_user)
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_steuern", {"aktion": "klick", "x": 10, "y": 20}),
        )
        assert argumente["autonom"] is True

    def test_das_modell_setzt_es_nicht_selbst(self, db: Session, regular_user: User):
        """Dieselbe Falle wie beim Aufraeumen, an einem zweiten Werkzeug."""
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_steuern", {"aktion": "klick", "autonom": True}),
        )
        assert argumente["autonom"] is False

    def test_maus_und_tastatur_haben_keinen_systembereich(
        self, db: Session, regular_user: User
    ):
        """Der Systembereich betrifft Pfade — ein Klick hat keinen.

        Das Feld hier trotzdem zu setzen waere ein Angebot an den naechsten
        Leser, es in `uebernahme.rs` auszuwerten, und dort bedeutete es nichts.
        """
        argumente = _desktop_argumente(
            db,
            user_id=regular_user.id,
            call=_aufruf("desktop_steuern", {"aktion": "klick"}),
        )
        assert "systembereich" not in argumente

    def test_eine_freigabebitte_im_autonomen_modus_wartet_auf_niemanden(self):
        """Die lange Frist haengt daran, dass jemand entscheiden **muss**.

        Im autonomen Modus antwortet der Rechner sofort und zeigt keine Karte
        (`auftrag::steuern`). Zehn Minuten Frist waeren dann nur zehn Minuten
        Stillstand, falls der Rechner gerade aus ist.
        """
        assert not desktop_job_service._wartet_auf_menschen(
            "desktop_steuern", {"aktion": "freigabe", "autonom": True}
        )
        assert desktop_job_service._wartet_auf_menschen(
            "desktop_steuern", {"aktion": "freigabe", "autonom": False}
        )
