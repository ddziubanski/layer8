# Development only: a throwaway Samba Active Directory DC (CORP.LOCAL) for testing
# the app's LDAP and Kerberos (portal SSO) code. Never use this image or its passwords anywhere real.
FROM debian:trixie-slim
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      samba samba-ad-dc samba-ad-provision samba-dsdb-modules samba-vfs-modules winbind \
      ldb-tools openssl ca-certificates procps \
 && rm -rf /var/lib/apt/lists/*
COPY entrypoint.sh seed.sh spn.sh computers.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/seed.sh /usr/local/bin/spn.sh /usr/local/bin/computers.sh
EXPOSE 88 389 636
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
