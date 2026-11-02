import io
import os
import unittest
import zipfile
from contextlib import redirect_stdout
from unittest.mock import patch

from app import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_report_does_not_expose_proxy_or_url_secrets(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"FLIBUSTA_SEARCH_URL": "https://catalog.test/search?q={searchTerms}&key=secret-key"}), \
             patch.object(diagnostics, "getproxies", return_value={"https": "http://user:secret-password@proxy.test"}), \
             patch.object(diagnostics, "probe", side_effect=lambda url, client: {"client": client, "ok": True}), redirect_stdout(output):
            self.assertEqual(diagnostics.main([]), 0)
        report = output.getvalue()
        self.assertIn("httpx_direct", report)
        self.assertIn("httpx_legacy_ua", report)
        self.assertIn("https", report)
        self.assertNotIn("secret", report)
        self.assertNotIn("proxy.test", report)

    def test_probe_does_not_expose_exception_details(self):
        with patch.object(diagnostics, "build_opener", side_effect=RuntimeError("secret-password")):
            result = diagnostics.probe("https://catalog.test/search", "urllib")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn("secret", str(result))

    def test_full_download_probe_verifies_epub_and_removes_temporary_file(self):
        from app import integrations
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("mimetype", b"application/epub+zip")
        paths = []
        def fetch(url, source, target=None, trace=None, **kwargs):
            paths.append(target.name)
            target.write(archive.getvalue())
            trace.append({"status": 200, "phase": "download"})
            return b"", url
        output = io.StringIO()
        with patch.object(integrations, "fetch_remote", side_effect=fetch), redirect_stdout(output):
            self.assertEqual(diagnostics.main(["--download", "https://catalog.test/book"]), 0)
        self.assertIn('"epub_valid": true', output.getvalue())
        from pathlib import Path
        self.assertFalse(Path(paths[0]).exists())
