"""Verified HTTPS using host trust, including packaged Linux Python runtimes."""

from __future__ import annotations

import logging
import os
import ssl
import sys
from pathlib import Path

logger = logging.getLogger("Client")

# Packaged OpenSSL may have build-machine defaults instead of the host's paths.
# These are distro-managed trust bundles, never certificates shipped by KH3.
LINUX_CA_FILES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Arch
    "/etc/pki/tls/certs/ca-bundle.crt",  # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",  # openSUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",  # Alpine
)


def create_download_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    # Explicit trust overrides belong to the operator. Do not broaden them.
    if sys.platform.startswith("linux") and not any(
        name in os.environ for name in ("SSL_CERT_FILE", "SSL_CERT_DIR")
    ):
        for filename in LINUX_CA_FILES:
            if not Path(filename).is_file():
                continue
            try:
                context.load_verify_locations(cafile=filename)
            except (OSError, ssl.SSLError) as exc:
                logger.warning("Could not load Linux system certificates from %s: %s", filename, exc)
                continue
            logger.debug("KH3 HTTPS loaded Linux system certificates from %s", filename)
            break
    try:
        context.set_alpn_protocols(["http/1.1"])
    except NotImplementedError:
        pass
    return context
