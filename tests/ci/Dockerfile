# Hosted-runner provisioning smoke environment. This is not a Proxmox/LXC test.
FROM debian:13
ENV container=docker
RUN apt-get update && apt-get install -y --no-install-recommends systemd systemd-sysv dbus python3 openssh-client \
    && rm -rf /var/lib/apt/lists/*
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
