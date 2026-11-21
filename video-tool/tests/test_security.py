from __future__ import annotations

import contextlib
import http.client
import io
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import app
from video_downloader import hls
from video_downloader.core import DownloadJob, JobConfig
from video_downloader.network import HeaderPolicy, parse_headers, trusted_origin


class HeaderScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cls.seen.append((self.server.server_port, self.path, dict(self.headers)))
                remote = f"http://127.0.0.1:{cls.remote.server_port}"
                if self.path.startswith("/redirect"):
                    location = remote + self.path.replace("/redirect", "/foreign", 1)
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path == "/same-redirect.m3u8":
                    self.send_response(302)
                    self.send_header("Location", "/index.m3u8")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self.path.endswith(".m3u8"):
                    prefix = remote + "/foreign" if self.path == "/cross.m3u8" else "/redirect" if self.path == "/nested.m3u8" else "/same"
                    body = f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{prefix}.key"\n#EXTINF:2,\n{prefix}.ts\n#EXT-X-ENDLIST\n'.encode()
                else:
                    body = bytes(range(16))
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        cls.remote = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.threads = []
        for server in (cls.source, cls.remote):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.source, cls.remote):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join()

    def setUp(self):
        self.seen.clear()

    def download(self, path, trusted=()):
        with tempfile.TemporaryDirectory() as directory:
            cfg = JobConfig(
                f"http://127.0.0.1:{self.source.server_port}{path}", Path(directory) / "out.mp4",
                headers="Cookie: session=synthetic-cookie\nAuthorization: Bearer synthetic-auth\nX-Account: synthetic-account",
                proxy="direct", max_retries=1, trusted_header_origins=trusted,
            )
            job = DownloadJob(cfg, Path("ffmpeg"), Path("ffprobe"))
            with patch.object(job, "_log"):
                hls.download(job, Path(directory) / "parts")

    def assert_credentials(self, records, expected):
        self.assertTrue(records)
        for _, _, headers in records:
            headers = {key.lower(): value for key, value in headers.items()}
            for name in ("cookie", "authorization", "x-account"):
                self.assertEqual(name in headers, expected)

    def test_same_origin_playlist_key_and_segment_keep_headers(self):
        self.download("/index.m3u8")
        self.assertEqual(len(self.seen), 3)
        self.assert_credentials(self.seen, True)

    def test_cross_origin_key_and_segment_drop_all_custom_headers(self):
        self.download("/cross.m3u8")
        self.assert_credentials([item for item in self.seen if item[0] == self.source.server_port], True)
        self.assert_credentials([item for item in self.seen if item[0] == self.remote.server_port], False)

    def test_cross_origin_playlist_redirect_drops_headers(self):
        self.download("/redirect.m3u8")
        self.assert_credentials([item for item in self.seen if item[0] == self.remote.server_port], False)

    def test_key_and_segment_redirects_drop_headers(self):
        self.download("/nested.m3u8")
        self.assert_credentials([item for item in self.seen if item[0] == self.remote.server_port], False)

    def test_same_origin_redirect_retains_headers(self):
        self.download("/same-redirect.m3u8")
        self.assert_credentials(self.seen, True)

    def test_explicit_trust_allows_only_the_exact_origin(self):
        self.download("/cross.m3u8", (f"http://127.0.0.1:{self.remote.server_port}",))
        self.assert_credentials(self.seen, True)

    def test_https_credentials_are_not_downgraded_even_with_trust(self):
        policy = HeaderPolicy("https://example.com/index.m3u8", "Cookie: secret", ("http://cdn.example.com",))
        self.assertEqual(policy.for_url("http://cdn.example.com/key"), {})
        self.assertEqual(policy.for_url("https://example.com:443/segment"), {"cookie": "secret"})
        self.assertEqual(policy.for_url("https://example.com:444/segment"), {})

    def test_trust_rejects_paths_credentials_and_non_http_schemes(self):
        for value in ("https://example.com/path", "https://user:secret@example.com", "file:///tmp/key", "https://example.com?token=secret", "https://example.com:0", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                trusted_origin(value)

    def test_invalid_and_transport_headers_are_rejected(self):
        for text in ("Cookie without colon", "Host: attacker.example", "Proxy-Authorization: secret", "X-Token: null\0byte"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_headers(text)


class HttpBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = app.LocalServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, method="POST", path="/api/test", body="{}", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def setUp(self):
        self.callback = Mock(return_value={"ok": True})
        patcher = patch.dict(app.ENDPOINTS, {"/api/test": (self.callback, 32, "32 B")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_local_json_request_reaches_the_callback(self):
        status, _, _ = self.request()
        self.assertEqual(status, 200)
        self.callback.assert_called_once_with({})

    def test_foreign_host_is_rejected(self):
        self.assertEqual(self.request(headers={"Host": "foreign.example", "Content-Type": "application/json"})[0], 403)
        self.callback.assert_not_called()

    def test_foreign_origin_is_rejected(self):
        self.assertEqual(self.request(headers={"Origin": "https://foreign.example", "Content-Type": "application/json"})[0], 403)
        self.callback.assert_not_called()

    def test_form_requests_cannot_trigger_an_action(self):
        self.assertEqual(self.request(body="x=1", headers={"Content-Type": "application/x-www-form-urlencoded"})[0], 415)
        self.callback.assert_not_called()

    def test_oversized_body_is_rejected_before_the_callback(self):
        self.assertEqual(self.request(body='{"value":"' + "x" * 40 + '"}')[0], 413)
        self.callback.assert_not_called()

    def test_invalid_json_and_non_object_payloads_are_rejected(self):
        for body in ("not json", "[]"):
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body)[0], 400)
        self.callback.assert_not_called()

    def test_private_files_are_not_static_resources(self):
        for path in ("/config.json", "/.task-index.json", "/../config.json"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, body=None)[0], 404)

    def test_page_responses_have_security_headers(self):
        status, headers, body = self.request("GET", "/", body=None)
        self.assertEqual(status, 200)
        self.assertTrue(body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

    def test_browser_icon_is_served_as_svg(self):
        status, headers, body = self.request("GET", "/shared/favicon.svg", body=None)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
        self.assertIn(b"<svg", body)


class DiagnosticPrivacyTest(unittest.TestCase):
    def make_job(self, **kwargs):
        cfg = JobConfig(
            "https://user:synthetic-password@example.com/signed-path/index.m3u8?token=synthetic-token",
            Path("unused.mp4"), headers="Cookie: session=synthetic-cookie\nAuthorization: Bearer synthetic-auth", **kwargs,
        )
        return DownloadJob(cfg, Path("ffmpeg"), Path("ffprobe"))

    def assert_private(self, text):
        for value in ("synthetic-password", "signed-path", "synthetic-token", "synthetic-cookie", "synthetic-auth"):
            self.assertNotIn(value, text)

    def test_command_urls_and_proxy_credentials_are_hidden(self):
        text = DownloadJob._redacted(["ffmpeg", "-http_proxy", "http://user:synthetic-password@proxy.example", "-i", self.make_job().cfg.url])
        self.assert_private(text)
        self.assertIn("example.com", text)

    def test_raw_stderr_is_sanitized_in_memory_and_terminal(self):
        job = self.make_job()
        diagnostic = job.cfg.url + "\nCookie: session=synthetic-cookie\nAuthorization: Bearer synthetic-auth\nsynthetic-token synthetic-auth"
        script = "import sys; sys.stderr.write(" + repr(diagnostic) + ")"
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            job._run_ffmpeg([sys.executable, "-c", script], 0, None)
        self.assertTrue(job.logs)
        self.assert_private("\n".join(job.logs))
        self.assert_private(console.getvalue())

    def test_error_message_is_also_sanitized(self):
        job = self.make_job()
        with patch.object(job, "_run", side_effect=RuntimeError("synthetic-auth " + job.cfg.url)), contextlib.redirect_stdout(io.StringIO()):
            job._run_guarded()
        self.assertEqual(job.progress.status, "failed")
        self.assert_private(job.progress.message)

    def test_legacy_refuses_credentials_before_probing(self):
        job = self.make_job(native_hls=False)
        with patch.object(job, "_probe") as probe:
            with self.assertRaisesRegex(ValueError, "兼容下载无法安全限制"):
                job._run()
        probe.assert_not_called()

    def test_unsupported_hls_does_not_fall_back_with_credentials(self):
        job = self.make_job()
        with patch.object(hls, "cached_playlist", return_value=None), patch.object(hls, "download", side_effect=hls.UnsupportedPlaylist("unsupported")), patch.object(job, "_probe") as probe, patch.object(job, "_log"):
            with self.assertRaisesRegex(ValueError, "兼容下载无法安全限制"):
                job._run()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
