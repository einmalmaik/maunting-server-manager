map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80 default_server;
    listen [::]:80 default_server;

    location = __BASE_PATH__ {
        return 301 __BASE_PATH__/;
    }

    location __BASE_PATH__/ {
        proxy_pass http://__BIND_HOST__:__BIND_PORT__/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Prefix __BASE_PATH__;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_connect_timeout       10s;
        proxy_send_timeout          60s;
        proxy_read_timeout          60s;
        proxy_next_upstream         error timeout;
        proxy_next_upstream_tries   2;
    }
}
