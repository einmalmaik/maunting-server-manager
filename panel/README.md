# Conan Exiles Enhanced Server Panel

This folder contains the optional web panel that sits on top of the existing Bash core.

## Stack

- FastAPI
- Jinja2 templates
- SQLAlchemy
- Alembic
- MariaDB
- Uvicorn
- Caddy reverse proxy on the configured panel domain

## Runtime Model

- The Bash core remains the source of truth and performs all Conan Exiles operations.
- The panel calls the existing manager through `subprocess`.
- Read-heavy pages use JSON bridge commands exposed through `./conanserver.sh panel bridge ...`.
- Mutating actions still run the same CLI commands as the terminal workflow.

## Installation

The panel is installed through the main Bash entrypoint:

```bash
sudo ./conanserver.sh panel install
```

That flow:

1. checks for a working Conan Exiles core install
2. installs Python, Caddy, MariaDB, and phpMyAdmin if missing
3. creates the MariaDB schema and panel DB user
4. creates the initial admin account
5. creates the Python virtual environment
6. installs the Python dependencies
7. writes the systemd unit and managed Caddy block
8. enables the panel under the configured domain or `http://<server-ip>`

## Development Notes

- The generated `.env` is not committed.
- The generated `.venv` is not committed.
- If you change the SQLAlchemy models, also add or update an Alembic migration.
