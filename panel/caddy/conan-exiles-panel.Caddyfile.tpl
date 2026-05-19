__SITE_ADDRESS__ {
    encode zstd gzip

    reverse_proxy __BIND_HOST__:__BIND_PORT__ {
        header_up X-Forwarded-Host {host}
        header_up X-Forwarded-Proto {scheme}
    }
}
