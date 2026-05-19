__SITE_ADDRESS__ {
    encode zstd gzip
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy same-origin
        X-Frame-Options DENY
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }

    reverse_proxy __BIND_HOST__:__BIND_PORT__ {
        header_up X-Forwarded-Host {host}
        header_up X-Forwarded-Proto {scheme}
    }
}
