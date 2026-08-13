from __future__ import annotations

import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / ".agents/skills/transcribe-media/scripts/download_media.py"
)
SPEC = importlib.util.spec_from_file_location("download_media", SCRIPT)
assert SPEC and SPEC.loader
download_media = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(download_media)


class MediaHandler(BaseHTTPRequestHandler):
    body = b"ID3-test-media"

    def do_GET(self) -> None:
        if self.path == "/start":
            self.send_response(302)
            self.send_header("Location", "/episode.mp3?signature=secret")
            self.end_headers()
            return
        if self.path.startswith("/episode.mp3"):
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(self.body)))
            self.end_headers()
            self.wfile.write(self.body)
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        return


class DownloadMediaTests(unittest.TestCase):
    def test_display_url_removes_credentials_query_and_fragment(self) -> None:
        actual = download_media.display_url(
            "https://user:password@example.com:8443/path/file.mp3?token=secret#part"
        )
        self.assertEqual(actual, "https://example.com:8443/path/file.mp3")

    def test_private_hosts_are_rejected_by_default(self) -> None:
        with self.assertRaises(SystemExit):
            download_media.validate_url("http://127.0.0.1/media.mp3")

    def test_download_follows_redirect_and_redacts_signed_query(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), MediaHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with TemporaryDirectory() as temporary:
                url = f"http://127.0.0.1:{server.server_port}/start"
                payload = download_media.download_media(
                    url,
                    Path(temporary),
                    allow_private_hosts=True,
                )
                self.assertEqual(Path(payload["path"]).read_bytes(), MediaHandler.body)
                self.assertTrue(payload["resolved_url"].endswith("/episode.mp3"))
                self.assertNotIn("secret", json.dumps(payload))
                self.assertTrue(payload["url_query_redacted"])
                self.assertTrue((Path(temporary) / "download.json").is_file())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
