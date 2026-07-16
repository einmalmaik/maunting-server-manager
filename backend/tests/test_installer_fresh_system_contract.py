"""Fresh-system invariants for every panel installation entrypoint."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _installer() -> str:
    return (ROOT / "install.sh").read_text(encoding="utf-8")


def test_all_panel_entrypoints_converge_on_the_same_installer() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    installer = _installer()

    assert 'exec bash "$repo_dir/install.sh" --simple --domain "$DOMAIN"' in bootstrap
    assert "if $SIMPLE_INSTALL" in installer
    assert "disable_caddy_source_for_apt_preflight" in installer
    assert "configure_caddy_repository" in installer
    assert "DEBIAN_FRONTEND" in installer


def test_bootstrap_survives_an_interrupted_caddy_repository_setup() -> None:
    bootstrap = (ROOT / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")

    disable_source = bootstrap.index('mv "$caddy_source" "$caddy_source_backup"')
    first_apt_update = bootstrap.index("apt-get update -qq")
    restore_source = bootstrap.index("restore_caddy_source\ntrap - EXIT")
    installer_exec = bootstrap.index('exec bash "$repo_dir/install.sh"')

    assert disable_source < first_apt_update < restore_source < installer_exec


def test_base_packages_cover_commands_used_before_feature_setup() -> None:
    installer = _installer()
    base_packages = installer.split('log "Installiere Basis-Pakete..."', 1)[1].split(
        "# ── Node.js 20", 1
    )[0]

    required_packages = {
        "ca-certificates",
        "curl",
        "git",
        "gnupg",
        "jq",
        "openssl",
        "python3",
        "python3-pip",
        "python3-venv",
        "rsync",
        "sudo",
        "uidmap",
        "dbus-user-session",
        "slirp4netns",
        "ufw",
        "iptables",
    }
    assert all(package in base_packages for package in required_packages)


def test_caddy_repository_is_repaired_before_it_can_block_apt() -> None:
    installer = _installer()

    preflight = installer.index("disable_caddy_source_for_apt_preflight\napt-get update")
    base_install = installer.index('log "Installiere Basis-Pakete..."')
    repository_setup = installer.index("configure_caddy_repository\napt-get update", base_install)
    caddy_install = installer.index('Dpkg::Options::="--force-confold" caddy')

    assert preflight < base_install < repository_setup < caddy_install
    assert "CADDY_SIGNING_FINGERPRINT" in installer
    assert "--show-keys --with-colons" in installer
    assert 'signed-by=$CADDY_KEYRING_FILE' in installer


def test_caddy_setup_is_atomic_and_preserves_existing_configuration() -> None:
    installer = _installer()

    assert "mktemp /usr/share/keyrings/.caddy-stable-keyring" in installer
    assert "mktemp /etc/apt/sources.list.d/.caddy-stable" in installer
    assert 'mv -f "$keyring_tmp" "$CADDY_KEYRING_FILE"' in installer
    assert 'mv -f "$source_tmp" "$CADDY_SOURCE_FILE"' in installer
    assert "--force-confold" in installer
    assert 'if [[ -f /etc/caddy/Caddyfile ]]' in installer


def test_postgres_role_setup_crosses_the_service_user_boundary_without_a_temp_secret() -> None:
    installer = _installer()

    assert "PG_SETUP_SQL" not in installer
    assert "CREATE USER msm WITH PASSWORD" in installer
    assert 'su - postgres -c "psql --no-psqlrc --set ON_ERROR_STOP=1"' in installer
    assert "PostgreSQL-Rolle konnte nicht eingerichtet werden" in installer


def test_simple_reinstall_honors_an_explicit_domain_without_resetting_other_settings() -> None:
    installer = _installer()

    assert 'if [[ -n "$INSTALL_DOMAIN" && "$INSTALL_DOMAIN" != "$CURRENT_DOMAIN" ]]' in installer
    assert 'DOMAIN="${INSTALL_DOMAIN:-$CURRENT_DOMAIN}"' in installer
    assert "KEEP_SETTINGS=true" in installer
    assert "CHANGED_DOMAIN=true" in installer
