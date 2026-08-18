FROM cm2network/steamcmd:root@sha256:e6b6b3503bf0e41feafe12dc709c90151afba193e1292cac55d28a7d470b1493 AS steamcmd

FROM ghcr.io/ptero-eggs/steamcmd:proton@sha256:c219f672cf6fedd11e811b7a41dc4ddab1fea659f77977bb956a939f2ef4e2a5

ARG UMU_VERSION=1.4.0
ARG UMU_SHA256=138ce4b8843608a257d4bee88191ca78a989778bcefd8abb3c1d1aaac3ac6fb8

LABEL org.opencontainers.image.source="https://github.com/einmalmaik/maunting-server-manager" \
      org.opencontainers.image.description="Pinned UMU + GE-Proton runtime for ARK: Survival Ascended" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

USER root
RUN mkdir -p /opt/umu-launcher /opt/proton/GE-Proton10-34 \
    && curl -fsSL "https://github.com/Open-Wine-Components/umu-launcher/releases/download/${UMU_VERSION}/umu-launcher-${UMU_VERSION}-zipapp.tar" -o /tmp/umu.tar \
    && echo "${UMU_SHA256}  /tmp/umu.tar" | sha256sum --check - \
    && tar -xf /tmp/umu.tar -C /opt/umu-launcher --strip-components=1 \
    && rm -f /tmp/umu.tar \
    && chmod 0755 /opt/umu-launcher/umu-run \
    && cp -a /usr/local/bin/. /opt/proton/GE-Proton10-34/ \
    && grep -Fqx '1774238111 GE-Proton10-34' /opt/proton/GE-Proton10-34/version

COPY --from=steamcmd /home/steam/steamcmd/linux32/steamclient.so /opt/steamcmd/linux32/steamclient.so
COPY --from=steamcmd /home/steam/steamcmd/linux64/steamclient.so /opt/steamcmd/linux64/steamclient.so
COPY entrypoint.sh /usr/local/bin/msm-asa-entrypoint
RUN chmod 0755 /usr/local/bin/msm-asa-entrypoint

ENV UMU_RUNTIME_UPDATE=0 \
    PROTONPATH=/opt/proton/GE-Proton10-34 \
    GAMEID=umu-2430930 \
    PROTON_USE_XALIA=0 \
    WINEDEBUG=-all \
    WINEESYNC=1 \
    WINEFSYNC=1

USER container
WORKDIR /home/container/ShooterGame/Binaries/Win64
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/msm-asa-entrypoint"]
