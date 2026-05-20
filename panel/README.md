# Maunting Server Manager

This folder contains the optional web panel that sits on top of the existing game server manager core.

## Stack

- FastAPI
- Jinja2 templates
- SQLAlchemy
- Alembic
- MariaDB / SQLite
- Uvicorn
- Caddy reverse proxy on the configured panel domain

## Runtime Model

- The Bash core remains the source of truth and performs all game server operations.
- The panel calls the existing manager through `subprocess`.
- Read-heavy pages use JSON bridge commands exposed through the game manager script.
- Mutating actions still run the same CLI commands as the terminal workflow.

## Installation

The panel is installed through the main Bash entrypoint:

```bash
sudo ./conanserver.sh panel install
```

That flow:

1. checks for a working Conan Exiles core install
2. installs Python, Caddy, MariaDB, and phpMyAdmin if requested
3. creates the MariaDB schema and panel DB user
4. writes a production `.env` with generated secrets and secure cookie defaults
5. creates the initial owner account without passing the password on the command line
6. creates the Python virtual environment
7. installs the Python dependencies
8. writes the systemd unit and managed Caddy block
9. enables the panel under the configured domain or `http://<server-ip>`

## Development Notes

- The generated `.env` is not committed.
- The generated `.venv` is not committed.
- If you change the SQLAlchemy models, also add or update an Alembic migration.
