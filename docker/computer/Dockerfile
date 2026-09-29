# OpenDot "Computer" sandbox image — DOT_SANDBOX=docker
#
# Debian + Xvfb + a light window manager + Chromium (reached over CDP for the
# agent's tools) + noVNC/websockify (so the Computer panel can also embed a
# full desktop view, not just the CDP screencast) + python3 for the shell.
#
# The host talks to a running container two ways:
#   - `docker exec <container> bash -lc <command>`   — the `shell` tool
#   - CDP over the published 9222 port                — the `browser` tool /
#     browse_task, exactly like the local backend, via
#     playwright.chromium.connect_over_cdp("http://127.0.0.1:<published-9222>")
#   - noVNC over the published 6080 port               — an optional full
#     "computer://<agent>/vnc" live view (opendot/computer/sandbox.py
#     DockerBackend.vnc_url())
#
# Build once and OpenDot picks it up automatically (DOT_SANDBOX=auto):
#   docker build -t opendot-computer docker/computer
# At run time the agent's home is bind-mounted at its host path and set as
# $HOME, and the container runs as your uid with memory/cpu/pid limits.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    HOME=/home/dot

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb fluxbox x11vnc \
        chromium chromium-sandbox fonts-liberation \
        python3 python3-pip python3-venv \
        git curl ca-certificates \
        websockify novnc \
    && rm -rf /var/lib/apt/lists/*

# noVNC's web client, served by websockify
RUN ln -s /usr/share/novnc /usr/share/novnc-www 2>/dev/null || true

RUN useradd -m -s /bin/bash dot
USER dot
WORKDIR /home/dot

# Chromium listens for CDP on 9222, bound to all interfaces inside the
# container (only reachable from the host via the published port mapping).
EXPOSE 9222 6080

USER root
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh
USER dot

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
