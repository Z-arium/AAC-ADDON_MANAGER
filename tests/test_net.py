import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aacam import net


class ContextTests(unittest.TestCase):
    def setUp(self):
        net.context.cache_clear()
        self.addCleanup(net.context.cache_clear)

    def test_uses_a_system_bundle_when_openssls_own_folders_are_missing(self):
        # What the Ubuntu-built AppImage sees on Arch: /usr/lib/ssl/cert.pem and certs/ don't exist.
        missing = ssl.DefaultVerifyPaths("/nope/cert.pem", "/nope/certs", "SSL_CERT_FILE", "/nope/cert.pem",
                                         "SSL_CERT_DIR", "/nope/certs")
        real = ssl.get_default_verify_paths().cafile or "/etc/ssl/certs/ca-certificates.crt"
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "ca.crt"
            bundle.write_bytes(Path(real).read_bytes())
            with mock.patch.object(ssl, "get_default_verify_paths", return_value=missing), \
                 mock.patch.object(net, "CA_FILES", ("/nope/a.crt", str(bundle))), \
                 mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("SSL_CERT_FILE", None)
                with mock.patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as make:
                    net.context()
        make.assert_called_with(cafile=str(bundle))

    def test_keeps_the_default_when_it_works(self):
        with mock.patch.object(net, "_has_default_cas", return_value=True), \
             mock.patch.object(ssl, "create_default_context", wraps=ssl.create_default_context) as make:
            net.context()
        make.assert_called_with()
