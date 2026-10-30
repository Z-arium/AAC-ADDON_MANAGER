"""HTTPS that works on every distro, including from the AppImage.

The AppImage bundles Python and OpenSSL built on Ubuntu, which look for CA certificates in
Ubuntu's folders. On Arch, Fedora or openSUSE those folders don't exist, so every HTTPS request
fails certificate checks. Point OpenSSL at whichever system bundle exists instead.
"""

from __future__ import annotations

import functools
import os
import ssl
import urllib.request

CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch, SteamOS
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/var/lib/ca-certificates/ca-bundle.pem",  # openSUSE
    "/etc/ssl/cert.pem",  # Alpine, Void
)


def _has_default_cas() -> bool:
    paths = ssl.get_default_verify_paths()
    if paths.cafile and os.path.isfile(paths.cafile):
        return True
    return bool(paths.capath and os.path.isdir(paths.capath) and os.listdir(paths.capath))


@functools.lru_cache(maxsize=1)
def context() -> ssl.SSLContext:
    if os.environ.get("SSL_CERT_FILE") or _has_default_cas():
        return ssl.create_default_context()
    for path in CA_FILES:
        if os.path.isfile(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


def urlopen(req, timeout: float):
    return urllib.request.urlopen(req, timeout=timeout, context=context())


def check(url: str = "https://api.github.com/rate_limit", timeout: float = 15) -> str | None:
    """None if HTTPS to GitHub works, else why not."""
    try:
        with urlopen(urllib.request.Request(url, headers={"User-Agent": "aacam"}), timeout=timeout):
            return None
    except Exception as e:  # report any failure, this is a diagnostic
        return str(getattr(e, "reason", e))
