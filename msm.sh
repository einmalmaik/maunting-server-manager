#!/bin/bash

#=======================================================================================#
#                         Maunting Server Manager
#=======================================================================================#
#     Adapted and maintained by:
#     Maik (MauntingStudios)
#---------------------------------------------------------------------------------------#
#     Original open-source credits:
#     @fiskce / @tootlejack / @thelastnoc / @haywardgg
#     License: GPL-3.0
#=======================================================================================#

### NO NEED TO EDIT ANYTHING IN THIS FILE ###
### Changes should be made in config.ini ###

CONFIG_FILE_NAME="config.ini"
CONFIG_FILE=""
LOCK_DIR="${HOME}/.msm.lock"

# ── Parse --server <name> before sourcing libs ────────────────────────────────
SERVER_NAME=""
_skip_next=0
_filtered_args=()
for _arg in "$@"; do
    if [ "$_skip_next" = "1" ]; then
        SERVER_NAME="$_arg"
        _skip_next=0
    elif [ "$_arg" = "--server" ]; then
        _skip_next=1
    else
        _filtered_args+=("$_arg")
    fi
done
set -- "${_filtered_args[@]+"${_filtered_args[@]}"}"
_missing_server_arg="$_skip_next"
unset _arg _skip_next _filtered_args
if [ "$_missing_server_arg" = "1" ]; then
    printf 'Error: --server requires a server name argument\n' >&2
    exit 1
fi
unset _missing_server_arg
export SERVER_NAME

# Validate SERVER_NAME before constructing SERVER_DIR
if [ -n "$SERVER_NAME" ] && [[ ! "$SERVER_NAME" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$|^[a-z0-9]$ ]]; then
    printf 'Error: invalid server name: %s\n' "$SERVER_NAME" >&2
    printf 'Server name must match ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ or be a single character [a-z0-9].\n' >&2
    exit 1
fi

# Set SERVER_DIR early so DEFAULT_CONFIG template references resolve correctly
SERVER_DIR="${HOME}/servers/${SERVER_NAME:-default}"
export SERVER_DIR
LOCK_HELD="0"
LOCK_ACTIVE_DIR=""
CURRENT_LANGUAGE="en"
SCRIPT_PATH=""
CURRENT_COMMAND=""
REQUESTED_COMMAND=""
REQUESTED_SUBCOMMAND=""
REQUESTED_THIRD_ARG=""
CONFIG_WAS_CREATED="0"
PROJECT_NAME="Maunting Server Manager"
PROJECT_MAINTAINER_NAME="Maik"
PROJECT_MAINTAINER_BRAND="MauntingStudios"
PROJECT_ORIGIN_AUTHORS="@fiskce / @tootlejack / @thelastnoc / @haywardgg"

default=""
bld=""
red=""
green=""
yellow=""
lightyellow=""
blue=""
lightblue=""
magenta=""
cyan=""
creeol=""

declare -a COMMAND_DISPLAY=()
declare -a COMMAND_ALIASES=()
declare -a COMMAND_HANDLER=()
declare -a COMMAND_SYNTAX=()
declare -a COMMAND_DESC_KEY=()
declare -a COMMAND_MUTATING=()
declare -a COMMAND_NEEDS_SERVER=()
declare -a COMMAND_REQUIRED_TOOLS=()
declare -a COMMAND_REQUIRE_RUNTIME_LIBS=()
declare -a COMMAND_REQUIRES_STEAMLOGIN=()
declare -A COMMAND_INDEX_BY_ALIAS=()
declare -A COMMAND_INDEX_BY_DISPLAY=()

SCRIPT_FILE="${BASH_SOURCE[0]}"
if command -v realpath >/dev/null 2>&1; then
    RESOLVED_SCRIPT_FILE="$(realpath "$SCRIPT_FILE" 2>/dev/null || true)"
    if [ -n "$RESOLVED_SCRIPT_FILE" ]; then
        SCRIPT_FILE="$RESOLVED_SCRIPT_FILE"
    fi
fi
while [ -L "$SCRIPT_FILE" ]; do
    SCRIPT_LINK_TARGET="$(readlink "$SCRIPT_FILE" 2>/dev/null || true)"
    [ -z "$SCRIPT_LINK_TARGET" ] && break
    if [[ "$SCRIPT_LINK_TARGET" = /* ]]; then
        SCRIPT_FILE="$SCRIPT_LINK_TARGET"
    else
        SCRIPT_FILE="$(dirname -- "$SCRIPT_FILE")/$SCRIPT_LINK_TARGET"
    fi
done
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$SCRIPT_FILE")" && pwd -P)"
LIB_DIR="${SCRIPT_DIR}/lib"
CONFIG_FILE="${SCRIPT_DIR}/${CONFIG_FILE_NAME}"

fn_source_required_module() {
    local module_name="$1"
    local module_path="${LIB_DIR}/${module_name}"

    if [ ! -f "$module_path" ]; then
        printf 'Error: required module not found: %s\n' "$module_path" >&2
        exit 1
    fi

    if ! bash -n "$module_path" >/dev/null 2>&1; then
        printf 'Error: module failed syntax check: %s\n' "$module_path" >&2
        exit 1
    fi

    if ! source "$module_path"; then
        printf 'Error: failed to load required module: %s\n' "$module_path" >&2
        exit 1
    fi
}

fn_source_required_module "i18n.sh"
fn_source_required_module "core.sh"
fn_source_required_module "config.sh"
fn_source_required_module "server.sh"
fn_source_required_module "install.sh"
fn_source_required_module "workshop.sh"
fn_source_required_module "backup.sh"
fn_source_required_module "autorestart.sh"
fn_source_required_module "ui.sh"
fn_source_required_module "panel.sh"
fn_source_required_module "commands.sh"

REQUESTED_COMMAND="${1:-help}"
REQUESTED_SUBCOMMAND="${2:-}"
REQUESTED_THIRD_ARG="${3:-}"

# ── Post-lib setup: migrate, override paths ───────────────────────────────────

# We skip auto-migration and directory creation for commands that just list things or manage servers
# to prevent unwanted re-creation of the 'default' server directory.
_SKIP_SETUP="0"
if [[ "$REQUESTED_COMMAND" =~ ^(help|server|panel|language)$ ]]; then
    if [ "$REQUESTED_COMMAND" = "server" ]; then
        # Skip setup for listing or deleting servers
        _SKIP_SETUP="1"
    elif [ "$REQUESTED_COMMAND" = "panel" ]; then
        if [ -z "$REQUESTED_SUBCOMMAND" ] || [[ "$REQUESTED_SUBCOMMAND" =~ ^(bridge|install|update|repair|status|reset-setup)$ ]]; then
            _SKIP_SETUP="1"
        fi
    else
        _SKIP_SETUP="1"
    fi
fi

if [ "$_SKIP_SETUP" = "0" ]; then
    fn_migrate_to_multiserver
fi

# Override CONFIG_FILE and LOCK_DIR to be per-server
CONFIG_FILE="${SERVER_DIR}/config.ini"
# Only move the lock inside the server directory for commands that already require the
# directory to exist (_SKIP_SETUP=0).  For meta-commands like "server create/delete" the
# directory may not exist yet, so keep the home-level lock to avoid a chicken-and-egg
# failure where fn_acquire_lock cannot create its mutex directory.
if [ "$_SKIP_SETUP" = "0" ]; then
    LOCK_DIR="${SERVER_DIR}/.msm.lock"
fi

# Verify the server directory exists. Do NOT auto-create it — that is the exclusive job of
# `server create <name>`. Auto-creating here is what silently produces ~/servers/default/.
if [ "$_SKIP_SETUP" = "0" ]; then
    if [ ! -d "${SERVER_DIR}" ]; then
        printf 'Error: server directory does not exist: %s\n' "${SERVER_DIR}" >&2
        printf 'Create it first with: %s server create %s\n' "$0" "${SERVER_NAME:-<name>}" >&2
        exit 1
    fi
fi

# Initialize all per-server path variables
fn_init_server_paths

SCRIPT_PATH="$(fn_resolve_script_path "$0")"

# Only ensure config exists and load it if we are not skipping setup
if [ "$_SKIP_SETUP" = "0" ]; then
    fn_ensure_config_exists
    fn_load_config
    fn_init_server_paths
elif [ "$REQUESTED_COMMAND" = "panel" ] && [ "$REQUESTED_SUBCOMMAND" = "bridge" ]; then
    case "$REQUESTED_THIRD_ARG" in
        status|backups|autorestart|workshop|mods)
            if [ -d "${SERVER_DIR}" ] && [ -f "${CONFIG_FILE}" ]; then
                fn_load_config
            fi
            ;;
    esac
fi

fn_init_colors
fn_register_commands
fn_checkroot_dayz
fn_dispatch_command "$REQUESTED_COMMAND" "${@:2}"
