#!/bin/bash

if [ -z "${SCRIPT_DIR:-}" ]; then
    printf 'ERROR: This script must be run via ./conanserver.sh, not directly.\n' >&2
    exit 1
fi

PANEL_DIR="${SCRIPT_DIR}/panel"
PANEL_ENV_FILE="${PANEL_DIR}/.env"
PANEL_ENV_EXAMPLE="${PANEL_DIR}/.env.example"
PANEL_VENV_DIR="${PANEL_DIR}/.venv"
PANEL_SERVICE_NAME="conan-exiles-panel"
PANEL_SERVICE_FILE="/etc/systemd/system/${PANEL_SERVICE_NAME}.service"
PANEL_CADDYFILE="/etc/caddy/Caddyfile"
PANEL_CADDY_BLOCK_NAME="CONAN EXILES PANEL"
PANEL_BIND_HOST="127.0.0.1"
PANEL_BIND_PORT="8710"
PANEL_BASE_PATH="/"
PANEL_PUBLIC_DOMAIN=""
PANEL_DB_HOST="127.0.0.1"
PANEL_DB_PORT="3306"
PANEL_DB_NAME="conan_panel"
PANEL_DB_USER="conan_panel"
PANEL_SESSION_COOKIE_NAME="conan_panel_session"
PANEL_COMMAND_TIMEOUT="1800"
PANEL_APT_UPDATED="0"

fn_panel_log() {
    local color="$1"
    local label="$2"
    shift 2 || true

    printf "[ ${color}%s${default} ] %s\n" "$label" "$*"
}

fn_panel_usage() {
    if [ "$CURRENT_LANGUAGE" = "de" ]; then
        printf '%s\n' 'Verwendung: panel install | panel update | panel repair | panel reset-setup | panel status [--json] | panel bridge <status|backups|autorestart|workshop|servers|legacy-check|reset-setup>'
    else
        printf '%s\n' 'Usage: panel install | panel update | panel repair | panel reset-setup | panel status [--json] | panel bridge <status|backups|autorestart|workshop|servers|legacy-check|reset-setup>'
    fi
}

fn_panel_require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fn_panel_log "$red" "FAIL" "Panel installation and repair must be run as root."
        return 1
    fi

    return 0
}

fn_panel_get_user_home() {
    local runtime_user="$1"
    getent passwd "$runtime_user" | cut -d ':' -f 6
}

fn_panel_core_installed_for_user() {
    local runtime_user="$1"
    local runtime_home=""

    runtime_home="$(fn_panel_get_user_home "$runtime_user")"
    [ -n "$runtime_home" ] || return 1
    # Accept new multi-server layout (servers/default/) or legacy flat layout
    [ -f "${runtime_home}/servers/default/steamcmd/steamcmd.sh" ] && [ -x "${runtime_home}/servers/default/serverfiles/ConanSandbox/Binaries/Linux/ConanSandboxServer" ] && return 0
    [ -f "${runtime_home}/steamcmd/steamcmd.sh" ] && [ -x "${runtime_home}/serverfiles/ConanSandbox/Binaries/Linux/ConanSandboxServer" ]
}

fn_panel_require_existing_core_for_user() {
    local runtime_user="$1"

    if ! fn_panel_core_installed_for_user "$runtime_user"; then
        fn_panel_log "$yellow" "WARN" "No Conan Exiles server installation found for user ${runtime_user}."
        fn_panel_log "$yellow" "INFO" "You can install the server later via the panel UI or run ./conanserver.sh install manually."
    fi

    return 0
}

fn_panel_require_supported_distro() {
    local distro_id=""
    local distro_like=""

    if [ ! -f /etc/os-release ]; then
        fn_panel_log "$red" "FAIL" "Unsupported system: /etc/os-release not found."
        return 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    distro_id="${ID:-}"
    distro_like="${ID_LIKE:-}"

    case " ${distro_id} ${distro_like} " in
        *" debian "*|*" ubuntu "*)
            return 0
            ;;
        *)
            fn_panel_log "$red" "FAIL" "Panel installer currently supports Debian/Ubuntu only."
            return 1
            ;;
    esac
}

fn_panel_prompt_yes_no() {
    local prompt="$1"
    local default_answer="${2:-Y}"
    local answer=""

    while true; do
        read -r -p "${prompt} [${default_answer}/n] " answer
        answer="${answer:-$default_answer}"
        if fn_is_yes "$answer"; then
            return 0
        fi
        if fn_is_no "$answer"; then
            return 1
        fi
        printf '%s\n' "Please answer yes or no."
    done
}

fn_panel_prompt_value() {
    local prompt="$1"
    local default_value="${2:-}"
    local value=""

    if [ -n "$default_value" ]; then
        read -r -p "${prompt} [${default_value}] " value
        value="${value:-$default_value}"
    else
        read -r -p "${prompt} " value
    fi

    printf '%s\n' "$value"
}

fn_panel_prompt_secret() {
    local prompt="$1"
    local value=""

    while true; do
        read -r -s -p "${prompt} " value
        printf '\n' >&2
        if [ -n "$value" ]; then
            printf '%s' "$value"
            return 0
        fi
        printf '%s\n' "Value cannot be empty." >&2
    done
}

fn_panel_command_exists() {
    command -v "$1" >/dev/null 2>&1
}

fn_panel_package_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

fn_panel_run_apt_update() {
    if [ "$PANEL_APT_UPDATED" = "1" ]; then
        return 0
    fi

    fn_panel_log "$lightblue" "INFO" "Refreshing apt package index..."
    if ! DEBIAN_FRONTEND=noninteractive apt-get update; then
        return 1
    fi
    PANEL_APT_UPDATED="1"
    return 0
}

fn_panel_install_missing_packages() {
    local label="$1"
    shift || true
    local -a missing=()
    local package=""

    for package in "$@"; do
        if ! fn_panel_package_installed "$package"; then
            missing+=("$package")
        fi
    done

    if [ "${#missing[@]}" -eq 0 ]; then
        fn_panel_log "$green" "OK" "${label} packages already present."
        return 0
    fi

    fn_panel_log "$yellow" "WARN" "Missing ${label} packages: ${missing[*]}"
    if ! fn_panel_prompt_yes_no "Install missing ${label} packages?" "Y"; then
        fn_panel_log "$red" "FAIL" "Cannot continue without ${label} packages."
        return 1
    fi

    fn_panel_run_apt_update || return 1
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing[@]}" || return 1
}

fn_panel_ensure_required_packages() {
    fn_panel_install_missing_packages "Python" python3 python3-venv python3-pip || return 1
    fn_panel_install_missing_packages "Caddy" caddy || return 1
    fn_panel_install_missing_packages "MariaDB" mariadb-server mariadb-client || return 1
    fn_panel_install_missing_packages "Scheduler" cron || return 1
    if fn_panel_prompt_yes_no "Install phpMyAdmin for database inspection? (optional)" "N"; then
        fn_panel_install_missing_packages "phpMyAdmin" phpmyadmin || return 1
    fi
}

fn_panel_ensure_nodejs() {
    if fn_panel_command_exists node && node --version 2>/dev/null | grep -q '^v2[0-9]'; then
        fn_panel_log "$green" "OK" "Node.js already installed."
        return 0
    fi

    fn_panel_log "$yellow" "INFO" "Installing Node.js 20 via APT keyring..."
    if ! fn_panel_command_exists curl; then
        fn_panel_run_apt_update || return 1
        DEBIAN_FRONTEND=noninteractive apt-get install -y curl || return 1
    fi
    if ! fn_panel_command_exists gpg; then
        fn_panel_run_apt_update || return 1
        DEBIAN_FRONTEND=noninteractive apt-get install -y gpg || return 1
    fi

    local keyring_dir="/usr/share/keyrings"
    local keyring_file="${keyring_dir}/nodesource.gpg"
    local list_file="/etc/apt/sources.list.d/nodesource.list"

    # Download and install GPG key (no script execution)
    local gpg_key
    if ! gpg_key="$(curl -fsSL "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key")"; then
        return 1
    fi
    printf '%s' "$gpg_key" | gpg --dearmor -o "$keyring_file" || return 1

    # Write APT source list
    printf 'deb [signed-by=%s] https://deb.nodesource.com/node_20.x nodistro main\n' "$keyring_file" \
        > "$list_file" || return 1

    DEBIAN_FRONTEND=noninteractive apt-get update -q || return 1
    DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs || return 1
    fn_panel_log "$green" "OK" "Node.js installed."
}

fn_panel_build_frontend() {
    local frontend_dir="${PANEL_DIR}/frontend"

    if [ ! -d "$frontend_dir" ]; then
        fn_panel_log "$yellow" "WARN" "Frontend directory not found at ${frontend_dir}. Skipping build."
        return 0
    fi

    fn_panel_log "$lightblue" "INFO" "Installing frontend npm dependencies..."
    if ! npm --prefix "$frontend_dir" ci --prefer-offline 2>/dev/null; then
        npm --prefix "$frontend_dir" install || return 1
    fi

    fn_panel_log "$lightblue" "INFO" "Building React frontend..."
    npm --prefix "$frontend_dir" run build || return 1
    fn_panel_log "$green" "OK" "Frontend built successfully → ${frontend_dir}/dist/"
}

fn_panel_get_runtime_user_default() {
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        printf '%s\n' "$SUDO_USER"
        return 0
    fi

    printf '%s\n' ""
}

fn_panel_require_user_exists() {
    local runtime_user="$1"

    if ! id "$runtime_user" >/dev/null 2>&1; then
        fn_panel_log "$red" "FAIL" "Linux user not found: $runtime_user"
        return 1
    fi

    return 0
}

fn_panel_escape_sed() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/[\/&|]/\\&/g' | sed -e ':a;N;$!ba;s/\n/\\n/g'
}

fn_panel_generate_secret() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
        return 0
    fi

    if command -v python3 >/dev/null 2>&1; then
        python3 - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
        return 0
    fi

    # Last resort: /dev/urandom — far stronger entropy than date-based hashing
    LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom | head -c 64
    printf '\n'
}

fn_panel_chown_runtime_paths() {
    local runtime_user="$1"
    local runtime_group=""

    runtime_group="$(id -gn "$runtime_user")"
    chown -R "$runtime_user:$runtime_group" "$PANEL_DIR"
    if [ -f "$PANEL_ENV_FILE" ]; then
        chown "root:$runtime_group" "$PANEL_ENV_FILE"
        chmod 640 "$PANEL_ENV_FILE"
    fi
}

fn_panel_fix_server_tree_permissions() {
    local runtime_user="$1"
    local runtime_home=""
    local runtime_group=""
    local server_dir=""
    local target=""
    local repaired_any="0"
    local had_error="0"

    runtime_home="$(fn_panel_get_user_home "$runtime_user")"
    if [ -z "$runtime_home" ]; then
        fn_panel_log "$red" "FAIL" "Could not determine home directory for runtime user ${runtime_user}."
        return 1
    fi
    runtime_group="$(id -gn "$runtime_user")"

    fn_panel_grant_runtime_rw() {
        local path="$1"
        if ! chmod -R u+rwX "$path"; then
            fn_panel_log "$red" "FAIL" "Failed to update permissions under ${path}."
            had_error="1"
        fi
    }

    if [ -d "${runtime_home}/servers" ]; then
        for server_dir in "${runtime_home}"/servers/*; do
            [ -d "$server_dir" ] || continue
            if ! chown -R "$runtime_user:$runtime_group" "$server_dir"; then
                fn_panel_log "$red" "FAIL" "Failed to update ownership under ${server_dir}."
                had_error="1"
            fi
            if [ -d "${server_dir}/serverfiles" ]; then
                fn_panel_grant_runtime_rw "${server_dir}/serverfiles"
            fi
            if [ -d "${server_dir}/backup" ]; then
                fn_panel_grant_runtime_rw "${server_dir}/backup"
            fi
            if [ -d "${server_dir}/steamcmd" ]; then
                fn_panel_grant_runtime_rw "${server_dir}/steamcmd"
            fi
            for target in \
                "${server_dir}/serverfiles/ConanSandbox/Binaries/Linux/ConanSandboxServer" \
                "${server_dir}/steamcmd/steamcmd.sh"; do
                if [ -f "$target" ]; then
                    if ! chmod u+rwx "$target"; then
                        fn_panel_log "$red" "FAIL" "Failed to repair executable bit on ${target}."
                        had_error="1"
                    fi
                fi
            done
            repaired_any="1"
        done
    fi

    for target in \
        "${runtime_home}/serverfiles" \
        "${runtime_home}/backup" \
        "${runtime_home}/steamcmd"; do
        if [ -e "$target" ]; then
            if ! chown -R "$runtime_user:$runtime_group" "$target"; then
                fn_panel_log "$red" "FAIL" "Failed to update ownership under ${target}."
                had_error="1"
            fi
            fn_panel_grant_runtime_rw "$target"
            repaired_any="1"
        fi
    done

    for target in \
        "${runtime_home}/serverfiles/ConanSandbox/Binaries/Linux/ConanSandboxServer" \
        "${runtime_home}/steamcmd/steamcmd.sh"; do
        if [ -f "$target" ]; then
            if ! chmod u+rwx "$target"; then
                fn_panel_log "$red" "FAIL" "Failed to repair executable bit on ${target}."
                had_error="1"
            fi
            repaired_any="1"
        fi
    done

    if [ "$had_error" = "1" ]; then
        return 1
    fi

    if [ "$repaired_any" = "1" ]; then
        fn_panel_log "$lightblue" "INFO" "Normalized server file ownership and executable bits for ${runtime_user}."
    fi
}

fn_panel_write_env_file() {
    local runtime_user="$1"
    local db_password="$2"
    local app_secret="$3"
    local runtime_group=""
    local https_only=""

    runtime_group="$(id -gn "$runtime_user")"
    PANEL_BASE_PATH="$(fn_panel_normalize_base_path "$PANEL_BASE_PATH")"
    https_only="$(fn_panel_https_only_default)"

    if ! cat > "$PANEL_ENV_FILE" <<EOF
PANEL_BIND_HOST="${PANEL_BIND_HOST}"
PANEL_BIND_PORT="${PANEL_BIND_PORT}"
PANEL_BASE_PATH="${PANEL_BASE_PATH}"
PANEL_PUBLIC_DOMAIN="${PANEL_PUBLIC_DOMAIN}"
PANEL_HTTPS_ONLY="${https_only}"
APP_ENV="production"
APP_SECRET_KEY="${app_secret}"
SESSION_COOKIE_NAME="${PANEL_SESSION_COOKIE_NAME}"
CONAN_MANAGER_PATH="${SCRIPT_PATH}"
PANEL_RUNTIME_USER="${runtime_user}"
PANEL_DB_HOST="${PANEL_DB_HOST}"
PANEL_DB_PORT="${PANEL_DB_PORT}"
PANEL_DB_NAME="${PANEL_DB_NAME}"
PANEL_DB_USER="${PANEL_DB_USER}"
PANEL_DB_PASSWORD="${db_password}"
DATABASE_URL="mysql+pymysql://${PANEL_DB_USER}:${db_password}@${PANEL_DB_HOST}:${PANEL_DB_PORT}/${PANEL_DB_NAME}?charset=utf8mb4"
PANEL_COMMAND_TIMEOUT="${PANEL_COMMAND_TIMEOUT}"
# Optional: Steam Web API key for Workshop browser in the panel.
# Get one at https://steamcommunity.com/dev/apikey
STEAM_API_KEY=""
# Email notifications. Use EMAIL_PROVIDER="smtp" for Gmail/SMTP or "resend" for Resend.
EMAIL_PROVIDER="off"
EMAIL_FROM=""
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_STARTTLS="true"
SMTP_USERNAME=""
SMTP_PASSWORD=""
RESEND_API_KEY=""
EOF
    then
        fn_panel_log "$red" "FAIL" "Failed to write panel env file: ${PANEL_ENV_FILE}"
        return 1
    fi

    chmod 640 "$PANEL_ENV_FILE"
    chown "root:$runtime_group" "$PANEL_ENV_FILE"
}

fn_panel_ensure_env_complete() {
    # Appends any optional .env keys that are absent in older installations.
    # Never deletes or overwrites entries that already exist.
    # Extend the array below when new optional keys are introduced.
    local -a optional_entries=(
        "APP_ENV|# Runtime mode for the FastAPI panel.|# Production enforces explicit secrets and secure defaults.|APP_ENV=\"production\""
        "PANEL_PUBLIC_DOMAIN|# Public panel domain used by Caddy. Leave empty for HTTP on the server IP.|# Example: panel.example.com|PANEL_PUBLIC_DOMAIN=\"\""
        "PANEL_HTTPS_ONLY|# Secure session cookies. Use true when Caddy serves the panel over HTTPS.|# Installer sets this automatically for domain-based Caddy installs.|PANEL_HTTPS_ONLY=\"false\""
        "STEAM_API_KEY|# Optional: Steam Web API key for Workshop browser in the panel.|# Get one at https://steamcommunity.com/dev/apikey|STEAM_API_KEY=\"\""
        "EMAIL_PROVIDER|# Email notifications. Use smtp for Gmail/SMTP, resend for Resend, or off.|# Secrets stay in this env file and are never written to config.ini.|EMAIL_PROVIDER=\"off\""
        "EMAIL_FROM|# Sender address for panel emails.|# Example: Conan Panel <panel@example.com>|EMAIL_FROM=\"\""
        "SMTP_HOST|# SMTP host for EMAIL_PROVIDER=smtp.|# Gmail default is smtp.gmail.com.|SMTP_HOST=\"smtp.gmail.com\""
        "SMTP_PORT|# SMTP port for EMAIL_PROVIDER=smtp.|# Use 587 with STARTTLS for Gmail.|SMTP_PORT=\"587\""
        "SMTP_STARTTLS|# Enable STARTTLS for SMTP.|# Set false only for trusted local relays.|SMTP_STARTTLS=\"true\""
        "SMTP_USERNAME|# SMTP username.|# For Gmail use the mailbox address with an app password.|SMTP_USERNAME=\"\""
        "SMTP_PASSWORD|# SMTP password or app password.|# Keep this secret.|SMTP_PASSWORD=\"\""
        "RESEND_API_KEY|# Resend API key for EMAIL_PROVIDER=resend.|# Keep this secret.|RESEND_API_KEY=\"\""
    )
    local entry key comment1 comment2 line appended="0"

    for entry in "${optional_entries[@]}"; do
        IFS='|' read -r key comment1 comment2 line <<< "$entry"
        if ! grep -q "^${key}=" "$PANEL_ENV_FILE" 2>/dev/null; then
            {
                printf '\n%s\n%s\n%s\n' "$comment1" "$comment2" "$line"
            } >> "$PANEL_ENV_FILE"
            fn_panel_log "$lightblue" "INFO" "Added missing env entry: ${key}"
            appended="1"
        fi
    done

    if [ "$appended" = "1" ]; then
        local runtime_group=""
        local runtime_user=""
        runtime_user="$(fn_panel_read_env_value PANEL_RUNTIME_USER 2>/dev/null || true)"
        if [ -n "$runtime_user" ]; then
            runtime_group="$(id -gn "$runtime_user" 2>/dev/null || true)"
        fi
        chmod 640 "$PANEL_ENV_FILE" 2>/dev/null || true
        if [ -n "$runtime_group" ]; then
            chown "root:$runtime_group" "$PANEL_ENV_FILE" 2>/dev/null || true
        fi
    fi
}

fn_panel_read_env_value() {
    local key="$1"
    local value=""

    if [ ! -f "$PANEL_ENV_FILE" ]; then
        return 1
    fi

    value="$(grep -E "^${key}=" "$PANEL_ENV_FILE" | tail -n 1 | cut -d '=' -f 2-)"
    value="${value#\"}"
    value="${value%\"}"
    printf '%s\n' "$value"
}

fn_panel_normalize_base_path() {
    local raw_path="${1:-/}"

    raw_path="${raw_path%%\?*}"
    raw_path="${raw_path%%#*}"
    raw_path="${raw_path%/}"
    if [ -z "$raw_path" ]; then
        printf '/\n'
        return 0
    fi
    case "$raw_path" in
        /*) printf '%s\n' "$raw_path" ;;
        *) printf '/%s\n' "$raw_path" ;;
    esac
}

fn_panel_prompt_public_domain() {
    local value=""

    value="$(fn_panel_prompt_value "Panel domain (blank = HTTP on server IP):" "${PANEL_PUBLIC_DOMAIN}")"
    value="${value#http://}"
    value="${value#https://}"
    value="${value%%/*}"
    value="$(printf '%s' "$value" | tr -d '[:space:]')"
    PANEL_PUBLIC_DOMAIN="$value"
}

fn_panel_site_address() {
    if [ -n "${PANEL_PUBLIC_DOMAIN:-}" ]; then
        printf '%s\n' "$PANEL_PUBLIC_DOMAIN"
        return 0
    fi

    printf ':80\n'
}

fn_panel_https_only_default() {
    if [ -n "${PANEL_PUBLIC_DOMAIN:-}" ]; then
        printf 'true\n'
    else
        printf 'false\n'
    fi
}

fn_panel_load_public_env_settings() {
    local env_base_path=""
    local env_domain=""

    env_base_path="$(fn_panel_read_env_value PANEL_BASE_PATH 2>/dev/null || true)"
    env_domain="$(fn_panel_read_env_value PANEL_PUBLIC_DOMAIN 2>/dev/null || true)"
    if [ -n "$env_base_path" ]; then
        PANEL_BASE_PATH="$(fn_panel_normalize_base_path "$env_base_path")"
    fi
    if [ -n "$env_domain" ]; then
        PANEL_PUBLIC_DOMAIN="$env_domain"
    fi
}

fn_panel_ensure_database() {
    local db_password="$1"
    local escaped_pw=""
    local sql=""

    # Escape characters that are special in SQL single-quoted strings or in the
    # double-quoted heredoc used to build the SQL statement.
    escaped_pw="${db_password//\\/\\\\}"   # \ → \\  (SQL + heredoc)
    escaped_pw="${escaped_pw//\'/\\\'}"    # ' → \'  (SQL)
    escaped_pw="${escaped_pw//\$/\\\$}"    # $ → \$  (heredoc shell expansion)
    escaped_pw="${escaped_pw//\`/\\\`}"    # ` → \`  (heredoc command substitution)

    sql=$(cat <<EOF
CREATE DATABASE IF NOT EXISTS \`${PANEL_DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${PANEL_DB_USER}'@'localhost' IDENTIFIED BY '${escaped_pw}';
CREATE USER IF NOT EXISTS '${PANEL_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_pw}';
ALTER USER '${PANEL_DB_USER}'@'localhost' IDENTIFIED BY '${escaped_pw}';
ALTER USER '${PANEL_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${escaped_pw}';
GRANT ALL PRIVILEGES ON \`${PANEL_DB_NAME}\`.* TO '${PANEL_DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${PANEL_DB_NAME}\`.* TO '${PANEL_DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
EOF
)

    if ! mysql -e "$sql"; then
        fn_panel_log "$red" "FAIL" "Failed to bootstrap MariaDB database/user for the panel."
        return 1
    fi

    return 0
}

fn_panel_sync_venv() {
    local runtime_user="$1"
    local runtime_group=""

    runtime_group="$(id -gn "$runtime_user")"

    if [ -z "${PANEL_VENV_DIR:-}" ] || [ "$PANEL_VENV_DIR" = "/" ]; then
        fn_panel_log "$red" "FAIL" "PANEL_VENV_DIR is unset or unsafe: ${PANEL_VENV_DIR:-<empty>}"
        return 1
    fi
    rm -rf "$PANEL_VENV_DIR"
    python3 -m venv "$PANEL_VENV_DIR" || return 1
    "${PANEL_VENV_DIR}/bin/pip" install --upgrade pip wheel || return 1
    "${PANEL_VENV_DIR}/bin/pip" install -r "${PANEL_DIR}/requirements.txt" || return 1
    chown -R "$runtime_user:$runtime_group" "$PANEL_VENV_DIR"
}

fn_panel_run_with_env() {
    (
        local key=""
        local value=""
        while IFS='=' read -r key value || [ -n "$key" ]; do
            case "$key" in
                ""|\#*)
                    continue
                    ;;
                *[!A-Z0-9_]*)
                    continue
                    ;;
            esac
            value="${value#\"}"
            value="${value%\"}"
            export "$key=$value"
        done < "$PANEL_ENV_FILE"
        cd "$PANEL_DIR" || exit 1
        "$@"
    )
}

fn_panel_run_migrations() {
    fn_panel_run_with_env "${PANEL_VENV_DIR}/bin/alembic" -c "${PANEL_DIR}/alembic.ini" upgrade head
}

fn_panel_create_admin_user() {
    local username="$1"
    local password="$2"

    # Pass password via env var, not CLI arg, so it does not appear in ps output.
    (
        export CONAN_PANEL_ADMIN_PASSWORD="$password"
        fn_panel_run_with_env "${PANEL_VENV_DIR}/bin/python" -m app.cli create-admin --username "$username" --force
    )
}

fn_panel_render_service_file() {
    local runtime_user="$1"
    local runtime_group=""
    local template_file="${PANEL_DIR}/systemd/conan-exiles-panel.service.tpl"

    runtime_group="$(id -gn "$runtime_user")"

    sed \
        -e "s|__RUNTIME_USER__|$(fn_panel_escape_sed "$runtime_user")|g" \
        -e "s|__RUNTIME_GROUP__|$(fn_panel_escape_sed "$runtime_group")|g" \
        -e "s|__PANEL_DIR__|$(fn_panel_escape_sed "$PANEL_DIR")|g" \
        -e "s|__ENV_FILE__|$(fn_panel_escape_sed "$PANEL_ENV_FILE")|g" \
        -e "s|__BIND_HOST__|$(fn_panel_escape_sed "$PANEL_BIND_HOST")|g" \
        -e "s|__BIND_PORT__|$(fn_panel_escape_sed "$PANEL_BIND_PORT")|g" \
        "$template_file" > "$PANEL_SERVICE_FILE"
}

fn_panel_render_caddy_file() {
    local template_file="${PANEL_DIR}/caddy/conan-exiles-panel.Caddyfile.tpl"
    local temp_file=""
    local site_address=""

    fn_panel_load_public_env_settings
    PANEL_BASE_PATH="$(fn_panel_normalize_base_path "$PANEL_BASE_PATH")"
    site_address="$(fn_panel_site_address)"
    temp_file="$(mktemp)" || return 1

    if [ -f "$PANEL_CADDYFILE" ]; then
        awk -v begin="# BEGIN ${PANEL_CADDY_BLOCK_NAME}" -v end="# END ${PANEL_CADDY_BLOCK_NAME}" '
            $0 == begin { skip = 1; next }
            $0 == end { skip = 0; next }
            skip != 1 { print }
        ' "$PANEL_CADDYFILE" > "$temp_file" || {
            rm -f "$temp_file"
            return 1
        }
        if [ -s "$temp_file" ]; then
            printf '\n' >> "$temp_file"
        fi
    fi

    {
        printf '# BEGIN %s\n' "$PANEL_CADDY_BLOCK_NAME"
        sed \
            -e "s|__SITE_ADDRESS__|$(fn_panel_escape_sed "$site_address")|g" \
            -e "s|__BIND_HOST__|$(fn_panel_escape_sed "$PANEL_BIND_HOST")|g" \
            -e "s|__BIND_PORT__|$(fn_panel_escape_sed "$PANEL_BIND_PORT")|g" \
            "$template_file"
        printf '# END %s\n' "$PANEL_CADDY_BLOCK_NAME"
    } >> "$temp_file"

    install -m 0644 "$temp_file" "$PANEL_CADDYFILE" || {
        rm -f "$temp_file"
        return 1
    }
    rm -f "$temp_file"
}

fn_panel_reload_services() {
    caddy validate --config "$PANEL_CADDYFILE" || return 1
    systemctl enable --now cron || return 1
    systemctl enable --now mariadb || return 1
    systemctl enable --now caddy || return 1
    systemctl daemon-reload || return 1
    systemctl enable "$PANEL_SERVICE_NAME" || return 1
    if systemctl is-active --quiet "$PANEL_SERVICE_NAME"; then
        systemctl restart "$PANEL_SERVICE_NAME" || return 1
    else
        systemctl start "$PANEL_SERVICE_NAME" || return 1
    fi
    systemctl reload caddy || return 1
}

fn_panel_setup_admin_credentials() {
    local username=""
    local password=""
    local password_confirm=""

    while true; do
        username="$(fn_panel_prompt_value "Initial admin username:" "admin")"
        if [ -n "$username" ]; then
            break
        fi
        printf '%s\n' "Username cannot be empty."
    done

    while true; do
        password="$(fn_panel_prompt_secret "Initial admin password:")"
        password_confirm="$(fn_panel_prompt_secret "Confirm admin password:")"
        if [ "$password" = "$password_confirm" ]; then
            PANEL_ADMIN_USERNAME="$username"
            PANEL_ADMIN_PASSWORD="$password"
            return 0
        fi
        printf '%s\n' "Passwords do not match. Try again."
    done
}

fn_panel_build_url() {
    local host_ip=""
    local scheme="http"

    fn_panel_load_public_env_settings

    if [ -n "${PANEL_PUBLIC_DOMAIN:-}" ]; then
        scheme="https"
        printf '%s://%s%s\n' "$scheme" "$PANEL_PUBLIC_DOMAIN" "$([ "$PANEL_BASE_PATH" = "/" ] && printf '' || printf '%s' "$PANEL_BASE_PATH")"
        return 0
    fi

    host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -z "$host_ip" ]; then
        host_ip="SERVER_IP"
    fi

    printf 'http://%s%s\n' "$host_ip" "$([ "$PANEL_BASE_PATH" = "/" ] && printf '' || printf '%s' "$PANEL_BASE_PATH")"
}

fn_panel_service_state() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl is-active "$PANEL_SERVICE_NAME" 2>/dev/null || true
        return 0
    fi

    printf '%s\n' "unknown"
}

fn_panel_proxy_state() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl is-active caddy 2>/dev/null || true
        return 0
    fi

    printf '%s\n' "unknown"
}

fn_panel_db_state() {
    local db_host=""
    local db_port=""
    local db_name=""
    local db_user=""
    local db_password=""

    if [ ! -f "$PANEL_ENV_FILE" ]; then
        printf '%s\n' "missing-config"
        return 0
    fi

    db_host="$(fn_panel_read_env_value PANEL_DB_HOST)"
    db_port="$(fn_panel_read_env_value PANEL_DB_PORT)"
    db_name="$(fn_panel_read_env_value PANEL_DB_NAME)"
    db_user="$(fn_panel_read_env_value PANEL_DB_USER)"
    db_password="$(fn_panel_read_env_value PANEL_DB_PASSWORD)"

    if MYSQL_PWD="$db_password" mysql --protocol=TCP --host="$db_host" --port="$db_port" --user="$db_user" --database="$db_name" -e "SELECT 1" >/dev/null 2>&1; then
        printf '%s\n' "connected"
    else
        printf '%s\n' "error"
    fi
}

fn_panel_cron_state() {
    if ! fn_is_crontab_installed; then
        printf '%s\n' "missing"
        return 0
    fi

    if fn_is_cron_service_active; then
        printf '%s\n' "active"
    else
        printf '%s\n' "inactive"
    fi
}

fn_json_escape() {
    local value="${1:-}"

    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\b'/\\b}"
    value="${value//$'\f'/\\f}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    value="${value//$'\t'/\\t}"
    # Escape remaining ASCII control characters U+0000–U+001F as JSON \uXXXX sequences
    value="${value//$'\001'/\\u0001}"
    value="${value//$'\002'/\\u0002}"
    value="${value//$'\003'/\\u0003}"
    value="${value//$'\004'/\\u0004}"
    value="${value//$'\005'/\\u0005}"
    value="${value//$'\006'/\\u0006}"
    value="${value//$'\007'/\\u0007}"
    value="${value//$'\016'/\\u000e}"
    value="${value//$'\017'/\\u000f}"
    value="${value//$'\020'/\\u0010}"
    value="${value//$'\021'/\\u0011}"
    value="${value//$'\022'/\\u0012}"
    value="${value//$'\023'/\\u0013}"
    value="${value//$'\024'/\\u0014}"
    value="${value//$'\025'/\\u0015}"
    value="${value//$'\026'/\\u0016}"
    value="${value//$'\027'/\\u0017}"
    value="${value//$'\030'/\\u0018}"
    value="${value//$'\031'/\\u0019}"
    value="${value//$'\032'/\\u001a}"
    value="${value//$'\033'/\\u001b}"
    value="${value//$'\034'/\\u001c}"
    value="${value//$'\035'/\\u001d}"
    value="${value//$'\036'/\\u001e}"
    value="${value//$'\037'/\\u001f}"
    printf '%s' "$value"
}

fn_panel_emit_status_json() {
    local service_state=""
    local proxy_state=""
    local db_state=""
    local cron_state=""
    local cron_installed="false"
    local cron_active="false"
    local runtime_user=""
    local url=""

    service_state="$(fn_panel_service_state)"
    proxy_state="$(fn_panel_proxy_state)"
    db_state="$(fn_panel_db_state)"
    cron_state="$(fn_panel_cron_state)"
    if fn_is_crontab_installed; then
        cron_installed="true"
    fi
    if fn_is_cron_service_active; then
        cron_active="true"
    fi
    runtime_user="$(fn_panel_read_env_value PANEL_RUNTIME_USER 2>/dev/null || true)"
    url="$(fn_panel_build_url)"

    printf '{'
    printf '"installed":%s,' "$([ -f "$PANEL_ENV_FILE" ] && printf 'true' || printf 'false')"
    printf '"service_state":"%s",' "$(fn_json_escape "$service_state")"
    printf '"proxy_name":"caddy",'
    printf '"proxy_state":"%s",' "$(fn_json_escape "$proxy_state")"
    printf '"nginx_state":"%s",' "$(fn_json_escape "$proxy_state")"
    printf '"database_state":"%s",' "$(fn_json_escape "$db_state")"
    printf '"cron_state":"%s",' "$(fn_json_escape "$cron_state")"
    printf '"cron_installed":%s,' "$cron_installed"
    printf '"cron_active":%s,' "$cron_active"
    printf '"cron_service_name":"%s",' "$(fn_json_escape "$(fn_get_cron_service_name)")"
    printf '"runtime_user":"%s",' "$(fn_json_escape "$runtime_user")"
    printf '"url":"%s"' "$(fn_json_escape "$url")"
    printf '}\n'
}

fn_panel_status_command() {
    local as_json="${1:-}"
    local body=""

    if [ "$as_json" = "--json" ]; then
        fn_panel_emit_status_json
        return 0
    fi

    body="Installed: $([ -f "$PANEL_ENV_FILE" ] && printf 'yes' || printf 'no')"
    body+=$'\n'"Service: $(fn_panel_service_state)"
    body+=$'\n'"Caddy: $(fn_panel_proxy_state)"
    body+=$'\n'"Database: $(fn_panel_db_state)"
    body+=$'\n'"Cron: $(fn_panel_cron_state)"
    body+=$'\n'"Runtime user: $(fn_panel_read_env_value PANEL_RUNTIME_USER 2>/dev/null || printf '-')"
    body+=$'\n'"URL: $(fn_panel_build_url)"

    printf '\n'
    fn_print_box "Panel Status" "90" "$body"
}

fn_panel_bridge_status() {
    local server_cfg=""
    local save_db=""
    local -a effective_times=()
    local backup_root=""
    local server_installed="false"
    local cron_installed="false"
    local cron_active="false"

    # Bridge commands skip fn_load_config (SKIP_SETUP=1), so source config directly
    # to make steamlogin, steampassword, language, etc. available for status reporting.
    if [ -f "${CONFIG_FILE:-}" ]; then
        # shellcheck disable=SC1090
        source "$CONFIG_FILE" 2>/dev/null || true
        case "${language:-en}" in
            en|de) CURRENT_LANGUAGE="${language:-en}" ;;
        esac
    fi

    server_cfg="$(fn_get_server_cfg_path)"
    save_db="${SERVERFILES}/${save_db_file:-ConanSandbox/Saved/game_0.db}"

    fn_status_dayz
    backup_root="${BACKUP_DIR}"
    mapfile -t effective_times < <(fn_get_effective_autorestart_times)
    if fn_server_installed; then
        server_installed="true"
    fi
    if fn_is_crontab_installed; then
        cron_installed="true"
    fi
    if fn_is_cron_service_active; then
        cron_active="true"
    fi

    local steamlogin_set="true"
    if [ "${steamlogin:-}" = "CHANGEME" ] || [ -z "${steamlogin:-}" ]; then
        steamlogin_set="false"
    fi

    local steampassword_set="true"
    if [ "${steamlogin:-}" != "anonymous" ]; then
        if [ "${steampassword:-}" = "CHANGEME" ] || [ -z "${steampassword:-}" ]; then
            steampassword_set="false"
        fi
    fi

    printf '{'
    printf '"server_installed":%s,' "$server_installed"
    printf '"server_running":%s,' "$([ "${dayzstatus:-0}" = "1" ] && printf 'true' || printf 'false')"
    printf '"steamlogin_set":%s,' "$steamlogin_set"
    printf '"steampassword_set":%s,' "$steampassword_set"
    printf '"mission_folder":"%s",' "$(fn_json_escape "$save_db")"
    printf '"config_path":"%s",' "$(fn_json_escape "$CONFIG_FILE")"
    printf '"manager_path":"%s",' "$(fn_json_escape "$SCRIPT_PATH")"
    printf '"language":"%s",' "$(fn_json_escape "$CURRENT_LANGUAGE")"
    printf '"backup_root":"%s",' "$(fn_json_escape "$backup_root")"
    printf '"cron_installed":%s,' "$cron_installed"
    printf '"cron_active":%s,' "$cron_active"
    printf '"cron_service_name":"%s",' "$(fn_json_escape "$(fn_get_cron_service_name)")"
    printf '"autorestart_mode":"%s",' "$(fn_json_escape "$autorestart_mode")"
    printf '"autorestart_summary":"%s",' "$(fn_json_escape "$(fn_get_autorestart_summary)")"
    printf '"effective_times":['
    if [ "${#effective_times[@]}" -gt 0 ]; then
        local index=""
        for ((index=0; index<${#effective_times[@]}; index++)); do
            if [ "$index" -gt 0 ]; then
                printf ','
            fi
            printf '"%s"' "$(fn_json_escape "${effective_times[$index]}")"
        done
    fi
    printf ']'
    printf '}\n'
}

fn_panel_bridge_backups() {
    local -a runs=()
    local index="0"
    local timestamp=""
    local run_dir=""
    local mission_present="false"
    local profile_present="false"

    mapfile -t runs < <(fn_list_backup_run_directories)

    printf '{"runs":['
    for ((index=0; index<${#runs[@]}; index++)); do
        timestamp="${runs[$index]}"
        run_dir="$(fn_get_backup_run_path "$timestamp")"
        mission_present="false"
        profile_present="false"
        if [ -f "$(fn_get_backup_mission_archive_path "$run_dir")" ]; then
            mission_present="true"
        fi
        if [ -f "$(fn_get_backup_profile_archive_path "$run_dir")" ]; then
            profile_present="true"
        fi
        if [ "$index" -gt 0 ]; then
            printf ','
        fi
        printf '{'
        printf '"timestamp":"%s",' "$(fn_json_escape "$timestamp")"
        printf '"mission_present":%s,' "$mission_present"
        printf '"profile_present":%s' "$profile_present"
        printf '}'
    done
    printf ']}\n'
}

fn_panel_bridge_autorestart() {
    local -a configured_times=()
    local -a effective_times=()
    local cron_installed="false"
    local cron_active="false"
    local scheduler_ready="false"
    local cron_block_present="false"
    local scheduler_error=""
    local index="0"

    if [ -n "${autorestart_times:-}" ]; then
        read -r -a configured_times <<< "$autorestart_times"
    fi
    mapfile -t effective_times < <(fn_get_autorestart_times_from_crontab)
    if fn_is_crontab_installed; then
        cron_installed="true"
    fi
    if fn_is_cron_service_active; then
        cron_active="true"
    fi
    if fn_is_scheduler_ready; then
        scheduler_ready="true"
    fi
    if fn_has_managed_crontab_block "CONANSERVER AUTORESTART ${SERVER_NAME:-default}"; then
        cron_block_present="true"
    fi
    scheduler_error="$(fn_get_scheduler_error)"

    printf '{'
    printf '"mode":"%s",' "$(fn_json_escape "$autorestart_mode")"
    printf '"configured_mode":"%s",' "$(fn_json_escape "$autorestart_mode")"
    printf '"mode_name":"%s",' "$(fn_json_escape "$(fn_get_autorestart_mode_name)")"
    printf '"summary":"%s",' "$(fn_json_escape "$(fn_get_autorestart_summary)")"
    printf '"interval_hours":"%s",' "$(fn_json_escape "$autorestart_interval_hours")"
    printf '"config_path":"%s",' "$(fn_json_escape "$CONFIG_FILE")"
    printf '"scheduler_ready":%s,' "$scheduler_ready"
    printf '"cron_installed":%s,' "$cron_installed"
    printf '"cron_active":%s,' "$cron_active"
    printf '"cron_service_name":"%s",' "$(fn_json_escape "$(fn_get_cron_service_name)")"
    printf '"cron_block_present":%s,' "$cron_block_present"
    printf '"scheduler_error":"%s",' "$(fn_json_escape "$scheduler_error")"
    printf '"log_path":"%s",' "$(fn_json_escape "$AUTORESTART_CRON_LOG")"
    printf '"effective_times":['
    for ((index=0; index<${#effective_times[@]}; index++)); do
        if [ "$index" -gt 0 ]; then
            printf ','
        fi
        printf '"%s"' "$(fn_json_escape "${effective_times[$index]}")"
    done
    printf '],'
    printf '"times":['
    for ((index=0; index<${#configured_times[@]}; index++)); do
        if [ "$index" -gt 0 ]; then
            printf ','
        fi
        printf '"%s"' "$(fn_json_escape "${configured_times[$index]}")"
    done
    printf ']'
    printf '}\n'
}

fn_panel_get_runtime_home() {
    if [ -z "${PANEL_RUNTIME_USER:-}" ] && [ -f "$PANEL_ENV_FILE" ]; then
        PANEL_RUNTIME_USER="$(fn_panel_read_env_value PANEL_RUNTIME_USER)"
    fi
    getent passwd "${PANEL_RUNTIME_USER:-$(id -un)}" | cut -d: -f6
}

fn_panel_bridge_mods_list() {
    local workshop_cfg workshop_val servermods_val
    local -a workshop_cfg_ids=()
    local -a ordered_entries=()
    local -A mod_name_by_id=()
    local -A mod_id_by_name=()
    local -A client_enabled_by_id=()
    local -A server_enabled_by_id=()
    local -A emitted_ids=()
    workshop_cfg="${WORKSHOP_CFG}"

    workshop_val=""
    servermods_val=""
    if [ -f "$CONFIG_FILE" ]; then
        # Strip CR characters to handle CRLF line endings from web editor
        workshop_val="$(grep -m1 '^workshop=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^workshop=//; s/^"//; s/"$//' || true)"
        servermods_val="$(grep -m1 '^servermods=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^servermods=//; s/^"//; s/"$//' || true)"
    fi

    local first="1" mod_id mod_name client_enabled server_enabled line entry
    if [ -f "$workshop_cfg" ]; then
        while IFS= read -r line || [ -n "$line" ]; do
            [[ "$line" =~ ^[[:space:]]*$ ]] && continue
            mod_id="$(printf '%s' "$line" | awk '{print $1}')"
            mod_name="$(printf '%s' "$line" | cut -d' ' -f2-)"
            [[ ! "$mod_id" =~ ^[0-9]+$ ]] && continue
            workshop_cfg_ids+=("$mod_id")
            mod_name_by_id["$mod_id"]="$mod_name"
            if [ -z "${mod_id_by_name[$mod_name]+x}" ]; then
                mod_id_by_name["$mod_name"]="$mod_id"
            fi
        done < "$workshop_cfg"
    fi

    IFS=';' read -r -a ordered_entries <<< "$workshop_val"
    for entry in "${ordered_entries[@]}"; do
        entry="${entry#@}"
        [ -n "$entry" ] || continue
        if [ -n "${mod_id_by_name[$entry]+x}" ]; then
            client_enabled_by_id["${mod_id_by_name[$entry]}"]="true"
        fi
    done

    IFS=';' read -r -a ordered_entries <<< "$servermods_val"
    for entry in "${ordered_entries[@]}"; do
        entry="${entry#@}"
        [ -n "$entry" ] || continue
        if [ -n "${mod_id_by_name[$entry]+x}" ]; then
            server_enabled_by_id["${mod_id_by_name[$entry]}"]="true"
        fi
    done

    printf '{"mods":['
    for line in "$workshop_val" "$servermods_val"; do
        IFS=';' read -r -a ordered_entries <<< "$line"
        for entry in "${ordered_entries[@]}"; do
            entry="${entry#@}"
            [ -n "$entry" ] || continue
            if [ -z "${mod_id_by_name[$entry]+x}" ]; then
                continue
            fi
            mod_id="${mod_id_by_name[$entry]}"
            if [ -n "${emitted_ids[$mod_id]+x}" ]; then
                continue
            fi
            emitted_ids["$mod_id"]="1"
            mod_name="${mod_name_by_id[$mod_id]}"
            client_enabled="${client_enabled_by_id[$mod_id]:-false}"
            server_enabled="${server_enabled_by_id[$mod_id]:-false}"
            [ "$first" != "1" ] && printf ','
            first="0"
            printf '{"id":"%s","name":"%s","client":%s,"server":%s}' \
                "$(fn_json_escape "$mod_id")" \
                "$(fn_json_escape "$mod_name")" \
                "$client_enabled" \
                "$server_enabled"
        done
    done

    for mod_id in "${workshop_cfg_ids[@]}"; do
        if [ -n "${emitted_ids[$mod_id]+x}" ]; then
            continue
        fi
        mod_name="${mod_name_by_id[$mod_id]}"
        client_enabled="${client_enabled_by_id[$mod_id]:-false}"
        server_enabled="${server_enabled_by_id[$mod_id]:-false}"
        [ "$first" != "1" ] && printf ','
        first="0"
        printf '{"id":"%s","name":"%s","client":%s,"server":%s}' \
            "$(fn_json_escape "$mod_id")" \
            "$(fn_json_escape "$mod_name")" \
            "$client_enabled" \
            "$server_enabled"
    done
    printf ']}\n'
}

fn_panel_bridge_mods_add() {
    local mod_id="${1:-}" mod_name="${2:-}"

    if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
        fn_panel_log "$red" "FAIL" "mods add: invalid mod_id"
        printf '{"error":"invalid mod_id"}\n'; return 1
    fi
    mod_name="${mod_name,,}"
    mod_name="${mod_name//$'\r'/}"
    mod_name="${mod_name//$'\n'/}"
    mod_name="${mod_name//$'\t'/ }"
    mod_name="${mod_name//;/}"
    mod_name="${mod_name//\"/}"
    mod_name="${mod_name//\//}"
    mod_name="${mod_name//\\/}"
    mod_name="$(printf '%s' "$mod_name" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
    while [[ "$mod_name" == .* ]]; do mod_name="${mod_name#.}"; done
    if [ -z "$mod_name" ] || [ "${#mod_name}" -gt 128 ]; then
        fn_panel_log "$red" "FAIL" "mods add: invalid mod_name"
        printf '{"error":"invalid mod_name"}\n'; return 1
    fi

    fn_acquire_lock "workshop"

    local workshop_cfg
    workshop_cfg="${WORKSHOP_CFG}"

    if [ -f "$workshop_cfg" ] && grep -qE "^${mod_id}([[:space:]]|$)" "$workshop_cfg"; then
        printf '{"error":"mod already exists"}\n'; return 1
    fi

    [ -f "$workshop_cfg" ] || install -m 600 /dev/null "$workshop_cfg"
    printf '%s %s\n' "$mod_id" "$mod_name" >> "$workshop_cfg"

    if [ -f "$CONFIG_FILE" ]; then
        local workshop_val new_workshop
        workshop_val="$(grep -m1 '^workshop=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^workshop=//; s/^"//; s/"$//' || true)"
        if [[ ";${workshop_val};" != *";@${mod_name};"* ]]; then
            new_workshop="${workshop_val:+${workshop_val};}@${mod_name}"
            fn_write_config_string "workshop" "${new_workshop:-}"
        fi
    fi

    printf '{"ok":true}\n'
}

fn_panel_bridge_mods_remove() {
    local mod_id="${1:-}"

    if [[ ! "$mod_id" =~ ^[0-9]+$ ]]; then
        printf '{"error":"invalid mod_id"}\n'; return 1
    fi

    fn_acquire_lock "workshop"

    local workshop_cfg
    workshop_cfg="${WORKSHOP_CFG}"

    if [ ! -f "$workshop_cfg" ]; then
        printf '{"ok":true}\n'; return 0
    fi

    local mod_name
    mod_name="$(grep -m1 "^${mod_id} " "$workshop_cfg" 2>/dev/null | cut -d' ' -f2-)"
    if [ -z "$mod_name" ]; then
        printf '{"error":"mod not found in workshop.cfg"}\n'; return 1
    fi
    local target_dir target_dir_resolved target_link link_target timestamp_file tmp_timestamp workshop_root
    workshop_root="$(readlink -f "${WORKSHOPFOLDER:-}" 2>/dev/null || printf '%s' "${WORKSHOPFOLDER:-}")"
    if [ -z "${WORKSHOPFOLDER:-}" ] || [ -z "$workshop_root" ] || [ "$workshop_root" = "/" ]; then
        printf '{"error":"unsafe WORKSHOPFOLDER"}\n'; return 1
    fi

    target_dir="${WORKSHOPFOLDER}/${mod_id}"
    target_dir_resolved="$(readlink -f "$target_dir" 2>/dev/null || printf '%s' "$target_dir")"
    case "$target_dir_resolved" in
        "$workshop_root"|"$workshop_root"/*) ;;
        *)
            printf '{"error":"unsafe mod path"}\n'; return 1
            ;;
    esac
    timestamp_file="${TIMESTAMP_FILE}"

    # Remove mod line from workshop.cfg
    sed -i "/^${mod_id}\b/d" "$workshop_cfg"

    # Remove @name from workshop= and servermods= in config.ini
    if [ -n "$mod_name" ] && [ -f "$CONFIG_FILE" ]; then
        local workshop_val servermods_val new_workshop new_servermods
        # Strip CR characters to handle CRLF line endings from web editor
        workshop_val="$(grep -m1 '^workshop=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^workshop=//; s/^"//; s/"$//' || true)"
        servermods_val="$(grep -m1 '^servermods=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^servermods=//; s/^"//; s/"$//' || true)"
        new_workshop="$(printf '%s' "$workshop_val" | tr ';' '\n' | grep -Fxv "@${mod_name}" | paste -sd ';' -)"
        new_servermods="$(printf '%s' "$servermods_val" | tr ';' '\n' | grep -Fxv "@${mod_name}" | paste -sd ';' -)"
        fn_write_config_string "workshop" "${new_workshop:-}"
        fn_write_config_string "servermods" "${new_servermods:-}"
    fi

    # Remove the @mod symlink only if it points to this specific workshop mod directory.
    if [ -n "$mod_name" ]; then
        target_link="${SERVERFILES}/@${mod_name}"
        if [ -L "$target_link" ]; then
            link_target="$(readlink -f "$target_link" 2>/dev/null || true)"
            if [ -n "$link_target" ] && [ "$link_target" = "$target_dir_resolved" ]; then
                rm -f -- "$target_link"
            fi
        fi
    fi

    if [ -e "$target_dir" ]; then
        rm -rf -- "$target_dir"
    fi

    if [ -f "$timestamp_file" ]; then
        tmp_timestamp="$(mktemp "${timestamp_file}.XXXXXX" 2>/dev/null || true)"
        if command -v jq >/dev/null 2>&1 && jq -e . "$timestamp_file" >/dev/null 2>&1; then
            if [ -n "$tmp_timestamp" ] && jq --arg mod_id "$mod_id" 'del(.[$mod_id])' "$timestamp_file" > "$tmp_timestamp"; then
                mv "$tmp_timestamp" "$timestamp_file"
            else
                rm -f -- "${tmp_timestamp:-}"
            fi
        elif command -v python3 >/dev/null 2>&1 && [ -n "$tmp_timestamp" ]; then
            if python3 - "$timestamp_file" "$tmp_timestamp" "$mod_id" <<'PY'
import json
import sys

source_path, output_path, mod_id = sys.argv[1:4]
with open(source_path, "r", encoding="utf-8") as handle:
    data = json.load(handle)
if not isinstance(data, dict):
    raise SystemExit(1)
data.pop(mod_id, None)
with open(output_path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, separators=(",", ":"))
PY
            then
                mv "$tmp_timestamp" "$timestamp_file"
            else
                rm -f -- "$tmp_timestamp"
            fi
        else
            rm -f -- "${tmp_timestamp:-}"
        fi
    fi

    printf '{"ok":true}\n'
}

fn_panel_bridge_mods_toggle() {
    local mod_id="${1:-}" mod_type="${2:-}" state="${3:-}"

    if [[ ! "$mod_id" =~ ^[0-9]+$ ]] || [[ ! "$mod_type" =~ ^(client|server)$ ]] || [[ ! "$state" =~ ^(on|off)$ ]]; then
        printf '{"error":"invalid arguments"}\n'; return 1
    fi

    fn_acquire_lock "workshop"

    local workshop_cfg mod_name
    workshop_cfg="${WORKSHOP_CFG}"

    mod_name="$(grep -m1 "^${mod_id} " "$workshop_cfg" 2>/dev/null | cut -d' ' -f2-)"
    if [ -z "$mod_name" ]; then
        printf '{"error":"mod not found in workshop.cfg"}\n'; return 1
    fi

    local config_key current_val new_val
    config_key="$( [ "$mod_type" = "client" ] && printf 'workshop' || printf 'servermods' )"
    # Strip CR characters to handle CRLF line endings from web editor
    current_val="$(grep -m1 "^${config_key}=" "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed "s/^${config_key}=//; s/^\"//; s/\"$//" || true)"

    if [ "$state" = "on" ]; then
        if [[ ";${current_val};" == *";@${mod_name};"* ]]; then
            printf '{"ok":true}\n'; return 0
        fi
        new_val="${current_val:+${current_val};}@${mod_name}"
    else
        new_val="$(printf '%s' "$current_val" | tr ';' '\n' | grep -Fxv "@${mod_name}" | paste -sd ';' -)"
    fi

    fn_write_config_string "$config_key" "${new_val:-}"
    printf '{"ok":true}\n'
}

fn_panel_bridge_workshop() {
    local workshop_cfg="${WORKSHOP_CFG}"
    local mod_count="0"
    local interval_minutes=""
    local display=""
    local cron_installed="false"
    local cron_active="false"
    local scheduler_ready="false"
    local cron_block_present="false"
    local scheduler_error=""

    if [ -f "$workshop_cfg" ]; then
        mod_count="$(grep -Ev '^\s*$' "$workshop_cfg" | wc -l | tr -d ' ')"
    fi
    interval_minutes="$(fn_get_workshop_autoupdate_interval_minutes_from_crontab)"
    display="$(fn_get_workshop_autoupdate_display "$interval_minutes")"
    if fn_is_crontab_installed; then
        cron_installed="true"
    fi
    if fn_is_cron_service_active; then
        cron_active="true"
    fi
    if fn_is_scheduler_ready; then
        scheduler_ready="true"
    fi
    if fn_has_managed_crontab_block "CONANSERVER WORKSHOP AUTOUPDATE ${SERVER_NAME:-default}"; then
        cron_block_present="true"
    fi
    scheduler_error="$(fn_get_scheduler_error)"

    printf '{'
    printf '"workshop_cfg":"%s",' "$(fn_json_escape "$workshop_cfg")"
    printf '"configured_mod_count":%s,' "${mod_count:-0}"
    printf '"autoupdate_enabled":%s,' "$([ -n "$interval_minutes" ] && printf 'true' || printf 'false')"
    if [ -n "$interval_minutes" ]; then
        printf '"autoupdate_interval_minutes":%s,' "$interval_minutes"
    else
        printf '"autoupdate_interval_minutes":null,'
    fi
    printf '"autoupdate_display":"%s",' "$(fn_json_escape "$display")"
    printf '"scheduler_ready":%s,' "$scheduler_ready"
    printf '"cron_installed":%s,' "$cron_installed"
    printf '"cron_active":%s,' "$cron_active"
    printf '"cron_service_name":"%s",' "$(fn_json_escape "$(fn_get_cron_service_name)")"
    printf '"autoupdate_cron_block_present":%s,' "$cron_block_present"
    printf '"scheduler_error":"%s",' "$(fn_json_escape "$scheduler_error")"
    printf '"autoupdate_log_path":"%s"' "$(fn_json_escape "$WORKSHOP_AUTOUPDATE_LOG")"
    printf '}\n'
}

fn_panel_bridge_mods_timestamps() {
    local timestamp_file
    timestamp_file="${TIMESTAMP_FILE}"
    if [ ! -f "$timestamp_file" ] || ! jq -e . "$timestamp_file" >/dev/null 2>&1; then
        printf '{"timestamps":{}}\n'
        return 0
    fi
    jq '{timestamps: .}' "$timestamp_file"
}

fn_panel_bridge_mods_reorder() {
    # Args: ordered list of mod IDs (all must already exist in workshop.cfg)
    local -a new_order=("$@")

    if [ "${#new_order[@]}" -eq 0 ]; then
        printf '{"error":"no mod IDs provided"}\n'; return 1
    fi

    fn_acquire_lock "workshop"

    local workshop_cfg="${WORKSHOP_CFG}"
    if [ ! -f "$workshop_cfg" ]; then
        printf '{"error":"workshop.cfg not found"}\n'; return 1
    fi

    # Validate: all IDs must be numeric
    for mod_id in "${new_order[@]}"; do
        if ! [[ "$mod_id" =~ ^[0-9]+$ ]]; then
            printf '{"error":"invalid mod_id: %s"}\n' "$mod_id"; return 1
        fi
    done

    # Build id->name map from existing file
    declare -A mod_map
    local line mod_id mod_name
    while IFS= read -r line || [ -n "$line" ]; do
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        mod_id="$(printf '%s' "$line" | awk '{print $1}')"
        mod_name="$(printf '%s' "$line" | cut -d' ' -f2-)"
        [[ ! "$mod_id" =~ ^[0-9]+$ ]] && continue
        mod_map["$mod_id"]="$mod_name"
    done < "$workshop_cfg"

    # Validate all requested IDs exist
    for mod_id in "${new_order[@]}"; do
        if [ -z "${mod_map[$mod_id]+x}" ]; then
            printf '{"error":"mod %s not found in workshop.cfg"}\n' "$mod_id"; return 1
        fi
    done

    # Rewrite file atomically to avoid data loss on failure
    local tmp_cfg
    tmp_cfg="$(mktemp "${workshop_cfg}.XXXXXX")" || { printf '{"error":"failed to create temp file"}\n'; return 1; }
    for mod_id in "${new_order[@]}"; do
        printf '%s %s\n' "$mod_id" "${mod_map[$mod_id]}" >> "$tmp_cfg"
    done
    mv "$tmp_cfg" "$workshop_cfg" || { rm -f "$tmp_cfg"; printf '{"error":"failed to update workshop.cfg"}\n'; return 1; }

    # Rebuild config.ini workshop= and servermods= in the new order,
    # preserving which mods are enabled as client vs server mods.
    if [ -f "$CONFIG_FILE" ]; then
        local workshop_val servermods_val new_workshop="" new_servermods="" name
        # Strip CR characters to handle CRLF line endings from web editor
        workshop_val="$(grep -m1 '^workshop=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^workshop=//; s/^"//; s/"$//' || true)"
        servermods_val="$(grep -m1 '^servermods=' "$CONFIG_FILE" 2>/dev/null | tr -d '\r' | sed 's/^servermods=//; s/^"//; s/"$//' || true)"
        for mod_id in "${new_order[@]}"; do
            name="${mod_map[$mod_id]}"
            if [[ ";${workshop_val};" == *";@${name};"* ]]; then
                new_workshop="${new_workshop:+${new_workshop};}@${name}"
            fi
            if [[ ";${servermods_val};" == *";@${name};"* ]]; then
                new_servermods="${new_servermods:+${new_servermods};}@${name}"
            fi
        done
        fn_write_config_string "workshop" "${new_workshop:-}"
        fn_write_config_string "servermods" "${new_servermods:-}"
    fi

    printf '{"ok":true}\n'
}

fn_panel_bridge_mods_update_selective() {
    local -a mod_ids=("$@")
    if [ "${#mod_ids[@]}" -eq 0 ]; then
        printf '{"error":"no mod IDs provided"}\n'
        return 1
    fi
    for mod_id in "${mod_ids[@]}"; do
        if ! [[ "$mod_id" =~ ^[0-9]+$ ]]; then
            printf '{"error":"invalid mod_id: %s"}\n' "$mod_id"
            return 1
        fi
    done
    fn_acquire_lock "workshop"
    fn_workshop_mods_selective "${mod_ids[@]}" \
        && printf '{"ok":true}\n' \
        || { printf '{"error":"selective update failed"}\n'; return 1; }
}

fn_panel_bridge_servers() {
    local runtime_home
    runtime_home="$(getent passwd "${SUDO_USER:-${USER:-$(whoami)}}" | cut -d: -f6)" || runtime_home="${HOME}"
    local servers_dir="${runtime_home}/servers"
    local first="1"

    printf '{"servers":['
    if [ -d "$servers_dir" ]; then
        while IFS= read -r name; do
            [ -z "$name" ] && continue
            [ "$first" != "1" ] && printf ','
            first="0"
            printf '{"name":"%s","server_dir":"%s"}' \
                "$(fn_json_escape "$name")" \
                "$(fn_json_escape "${servers_dir}/${name}")"
        done < <(find "$servers_dir" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort)
    fi
    printf '],"current":"%s"}\n' "$(fn_json_escape "${SERVER_NAME:-default}")"
}

fn_panel_bridge_command() {
    local topic="${1:-}"

    case "$topic" in
        status)
            fn_panel_bridge_status
            ;;
        backups)
            fn_panel_bridge_backups
            ;;
        autorestart)
            fn_panel_bridge_autorestart
            ;;
        workshop)
            fn_panel_bridge_workshop
            ;;
        servers)
            fn_panel_bridge_servers
            ;;
        legacy-check)
            local _legacy_home
            _legacy_home="$(getent passwd "${SUDO_USER:-${USER:-$(whoami)}}" | cut -d: -f6)" || _legacy_home="${HOME}"
            if [ -d "${_legacy_home}/serverfiles" ] || [ -d "${_legacy_home}/steamcmd" ]; then
                printf '{"legacy":true}\n'
            else
                printf '{"legacy":false}\n'
            fi
            ;;
        mods)
            local subcmd="${2:-list}"
            case "$subcmd" in
                list)       fn_panel_bridge_mods_list ;;
                add)        fn_panel_bridge_mods_add "${3:-}" "${4:-}" ;;
                remove)     fn_panel_bridge_mods_remove "${3:-}" ;;
                toggle)     fn_panel_bridge_mods_toggle "${3:-}" "${4:-}" "${5:-}" ;;
                timestamps) fn_panel_bridge_mods_timestamps ;;
                update)     fn_panel_bridge_mods_update_selective "${@:3}" ;;
                reorder)    fn_panel_bridge_mods_reorder "${@:3}" ;;
                *)
                    fn_panel_log "$red" "FAIL" "Unknown mods bridge subcommand: ${subcmd}"
                    return 1
                    ;;
            esac
            ;;
        reset-setup)
            fn_panel_run_with_env "${PANEL_VENV_DIR}/bin/python" - <<'PYEOF'
import sys
from app.database import SessionLocal
from app.models import User
try:
    with SessionLocal() as db:
        deleted = db.query(User).delete()
        db.commit()
        print('{"ok":true,"deleted":' + str(deleted) + '}')
except Exception as e:
    err = str(e).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')
    print('{"ok":false,"error":"' + err + '"}', file=sys.stderr)
    sys.exit(1)
PYEOF
            ;;
        *)
            fn_panel_log "$red" "FAIL" "Unknown panel bridge topic: ${topic:-<empty>}"
            return 1
            ;;
    esac
}

fn_panel_install_command() {
    local runtime_user=""
    local db_password=""
    local app_secret=""
    local default_runtime_user=""

    fn_panel_require_root || return 1
    fn_panel_require_supported_distro || return 1

    if [ -f "$PANEL_ENV_FILE" ]; then
        if ! fn_panel_prompt_yes_no "Panel config already exists. Overwrite and reinstall panel configuration?" "N"; then
            fn_panel_log "$yellow" "WARN" "Aborting install. Use ./conanserver.sh panel repair for non-destructive fixes."
            return 1
        fi
    fi

    fn_panel_ensure_required_packages || return 1
    systemctl enable --now cron || return 1
    systemctl enable --now mariadb || return 1
    systemctl enable --now caddy || return 1

    default_runtime_user="$(fn_panel_get_runtime_user_default)"
    while true; do
        runtime_user="$(fn_panel_prompt_value "Panel runtime Linux user:" "$default_runtime_user")"
        fn_panel_require_user_exists "$runtime_user" && break
    done
    fn_panel_prompt_public_domain
    fn_panel_setup_admin_credentials || return 1
    fn_panel_require_existing_core_for_user "$runtime_user" || return 1

    db_password="$(fn_panel_generate_secret)"
    app_secret="$(fn_panel_generate_secret)"

    fn_panel_ensure_database "$db_password" || return 1
    fn_panel_write_env_file "$runtime_user" "$db_password" "$app_secret" || return 1
    fn_panel_sync_venv "$runtime_user" || return 1
    fn_panel_ensure_nodejs || return 1
    fn_panel_build_frontend || return 1
    fn_panel_run_migrations || return 1
    fn_panel_create_admin_user "$PANEL_ADMIN_USERNAME" "$PANEL_ADMIN_PASSWORD" || return 1
    unset PANEL_ADMIN_USERNAME PANEL_ADMIN_PASSWORD
    fn_panel_render_service_file "$runtime_user" || return 1
    fn_panel_render_caddy_file || return 1
    fn_panel_chown_runtime_paths "$runtime_user"
    fn_panel_fix_server_tree_permissions "$runtime_user" || return 1
    fn_panel_reload_services || return 1

    fn_panel_log "$green" "OK" "Panel installed successfully. Visit the panel URL in your browser to create your owner account."
    fn_panel_log "$lightblue" "INFO" "Open $(fn_panel_build_url) after allowing the server port through your firewall."
}

fn_panel_update_command() {
    local runtime_user=""

    fn_panel_require_root || return 1
    fn_panel_require_supported_distro || return 1

    if [ ! -f "$PANEL_ENV_FILE" ]; then
        fn_panel_log "$red" "FAIL" "Panel not installed. Run ./conanserver.sh panel install first."
        return 1
    fi

    runtime_user="$(fn_panel_read_env_value PANEL_RUNTIME_USER)"
    fn_panel_require_user_exists "$runtime_user" || return 1
    fn_panel_install_missing_packages "Scheduler" cron || return 1
    systemctl enable --now cron || return 1

    if git -C "$SCRIPT_DIR" remote get-url origin >/dev/null 2>&1; then
        fn_panel_log "$lightblue" "INFO" "Pulling latest code..."
        git -C "$SCRIPT_DIR" pull --ff-only || return 1
    else
        fn_panel_log "$yellow" "WARN" "No git remote found — skipping pull, rebuilding from current files."
    fi

    fn_panel_sync_venv "$runtime_user" || return 1
    fn_panel_ensure_nodejs || return 1
    fn_panel_build_frontend || return 1
    fn_panel_run_migrations || return 1
    fn_panel_render_service_file "$runtime_user" || return 1
    fn_panel_render_caddy_file || return 1
    fn_panel_chown_runtime_paths "$runtime_user"
    fn_panel_reload_services || return 1

    fn_panel_log "$green" "OK" "Panel updated successfully."
}

fn_panel_repair_command() {
    local runtime_user=""

    fn_panel_require_root || return 1
    fn_panel_require_supported_distro || return 1

    if [ ! -f "$PANEL_ENV_FILE" ]; then
        fn_panel_log "$red" "FAIL" "Panel env file not found. Run ./conanserver.sh panel install first."
        return 1
    fi

    runtime_user="$(fn_panel_read_env_value PANEL_RUNTIME_USER)"
    fn_panel_require_user_exists "$runtime_user" || return 1
    fn_panel_require_existing_core_for_user "$runtime_user" || return 1
    fn_panel_ensure_env_complete
    fn_panel_ensure_required_packages || return 1
    systemctl enable --now cron || return 1
    systemctl enable --now mariadb || return 1
    systemctl enable --now caddy || return 1
    fn_panel_sync_venv "$runtime_user" || return 1
    fn_panel_ensure_nodejs || return 1
    fn_panel_build_frontend || return 1
    fn_panel_run_migrations || return 1
    fn_panel_render_service_file "$runtime_user" || return 1
    fn_panel_render_caddy_file || return 1
    fn_panel_chown_runtime_paths "$runtime_user"
    fn_panel_fix_server_tree_permissions "$runtime_user" || return 1
    fn_panel_reload_services || return 1

    fn_panel_log "$green" "OK" "Panel repair completed."
}

fn_panel_command() {
    local subcommand="${1:-}"
    shift || true

    case "$subcommand" in
        install)
            fn_panel_install_command "$@"
            ;;
        update)
            fn_panel_update_command "$@"
            ;;
        repair)
            fn_panel_repair_command "$@"
            ;;
        status)
            fn_panel_status_command "$@"
            ;;
        bridge)
            fn_panel_bridge_command "$@"
            ;;
        reset-setup)
            fn_panel_require_root || return 1
            fn_panel_log "$yellow" "WARN" "This will delete ALL panel users. The setup page will appear on next login."
            printf 'Are you sure? [y/N] '
            local confirm=""
            read -r confirm
            if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
                fn_panel_log "$yellow" "INFO" "Aborted."
                return 0
            fi
            fn_panel_bridge_command "reset-setup" || return 1
            fn_panel_log "$green" "OK" "All users removed. Visit the panel to create a new owner account."
            ;;
        *)
            fn_panel_usage
            return 1
            ;;
    esac
}
