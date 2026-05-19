[Unit]
Description=Conan Exiles Enhanced Server Panel
After=network.target mariadb.service
Wants=mariadb.service

[Service]
Type=exec
User=__RUNTIME_USER__
Group=__RUNTIME_GROUP__
WorkingDirectory=__PANEL_DIR__
EnvironmentFile=__ENV_FILE__
ExecStart=__PANEL_DIR__/.venv/bin/gunicorn \
    -k uvicorn.workers.UvicornWorker \
    -w 2 \
    --bind __BIND_HOST__:__BIND_PORT__ \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    app.main:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
