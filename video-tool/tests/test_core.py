from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_downloader import api, config, system_proxy
from video_downloader.core import DownloadJob, JobConfig, child_env, proxy_args, public_proxy
from video_downloader.locator import Tools


class SafeFilenameTest(unittest.TestCase):
    def test_m3u8_becomes_mp4(self):
        self.assertEqual(api.safe_filename("video.m3u8", "https://example.com/a.m3u8"), "video.mp4")

    def test_path_components_are_stripped(self):
        self.assertEqual(api.safe_filename("../../evil.mp4", "https://example.com/a"), "evil.mp4")

    def test_invalid_windows_characters_are_replaced(self):
        self.assertEqual(api.safe_filename('a<b>c:d.mp4', "https://example.com/a"), "a_b_c_d.mp4")

    def test_unknown_suffix_gets_mp4(self):
        self.assertEqual(api.safe_filename("clip", "https://example.com/a"), "clip.mp4")

    def test_empty_name_uses_url_path(self):
        self.assertEqual(api.safe_filename("", "https://example.com/path/video.mp4"), "video.mp4")


class RedactTest(unittest.TestCase):
    def test_headers_are_hidden(self):
        command = ["ffmpeg", "-headers", "Cookie: secret", "-i", "https://example.com/a.m3u8"]
        text = DownloadJob._redacted(command)
        self.assertNotIn("secret", text)
        self.assertIn("<已隐藏>", text)


class ConfigTest(unittest.TestCase):
    def test_defaults_when_file_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "CONFIG_PATH", Path(directory) / "missing.json"):
                cfg = config.load()
        self.assertEqual(cfg["port"], 8766)
        self.assertIn("output_dir", cfg)
        self.assertFalse(cfg["keep_parts"])


class ProxyTest(unittest.TestCase):
    def test_custom_proxy_args(self):
        self.assertEqual(
            proxy_args("http://127.0.0.1:7890"),
            ["-http_proxy", "http://127.0.0.1:7890"],
        )

    def test_direct_has_no_proxy_args(self):
        self.assertEqual(proxy_args("direct"), [])
        self.assertEqual(proxy_args(""), [])

    def test_direct_strips_proxy_environment(self):
        with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:1"}, clear=False):
            env = child_env("direct")
        self.assertNotIn("HTTP_PROXY", env)

    def test_custom_proxy_strips_no_proxy(self):
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1"},
            clear=False,
        ):
            env = child_env("http://127.0.0.1:7890")
        self.assertNotIn("HTTP_PROXY", env)
        self.assertNotIn("NO_PROXY", env)

    def test_socks_proxy_is_rejected(self):
        with self.assertRaises(ValueError):
            JobConfig(
                url="http://example.com/a.m3u8",
                output=Path("out.mp4"),
                proxy="socks5://127.0.0.1:7890",
            )

    def test_public_proxy_hides_credentials(self):
        self.assertEqual(
            public_proxy("http://user:secret@127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_invalid_proxy_ports_are_rejected_before_creating_a_job(self):
        for proxy in ("http://127.0.0.1:bad-port", "http://127.0.0.1:0", "http://127.0.0.1:65536"):
            with self.subTest(proxy=proxy), self.assertRaises(ValueError):
                JobConfig("https://example.com/a.m3u8", Path("out.mp4"), proxy=proxy)

    def test_invalid_proxy_is_not_used_for_a_connection_test(self):
        with patch("video_downloader.api.socket.create_connection") as connect:
            with self.assertRaises(ValueError):
                api.test_proxy({"proxy": "http://127.0.0.1:bad-port"})
        connect.assert_not_called()

    def test_public_ipv6_proxy_has_brackets_and_no_credentials(self):
        self.assertEqual(public_proxy("http://user:secret@[::1]:7890"), "http://[::1]:7890")

    def test_parse_plain_windows_proxy(self):
        self.assertEqual(
            system_proxy.parse_server("127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_parse_mapped_windows_proxy(self):
        self.assertEqual(
            system_proxy.parse_server(
                "http=127.0.0.1:7890;https=127.0.0.1:7890;socks=127.0.0.1:7891"
            ),
            "http://127.0.0.1:7890",
        )

    def test_proxy_test_rejects_direct(self):
        with self.assertRaises(ValueError):
            api.test_proxy({"proxy": "direct"})


class ApiStateTest(unittest.TestCase):
    def test_state_shape(self):
        with patch.object(api, "TOOLS", None), patch.object(api, "TOOLS_ERROR", ""), \
                patch.object(api, "JOBS", {}), patch.object(api, "refresh_tools", return_value=None):
            state = api.state({})
        self.assertIn("tools", state)
        self.assertIn("config", state)
        self.assertIn("jobs", state)
        self.assertEqual(state["jobs"], [])

    def test_start_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            api.start({"url": "ftp://example.com/a.m3u8", "out_dir": "."})

    def test_logs_requires_job_id(self):
        with self.assertRaises(ValueError):
            api.logs({})

    def test_clear_finished_empty(self):
        with patch.object(api, "JOBS", {}):
            result = api.clear_finished({})
        self.assertEqual(result["removed"], 0)


class BatchFilenameTest(unittest.TestCase):
    def run_batch(self, filename="", filenames=None, existing=()):
        with tempfile.TemporaryDirectory() as directory:
            for name in existing:
                (Path(directory) / name).touch()
            tools = Tools(Path("ffmpeg"), Path("ffprobe"), "test")
            with patch.object(api, "TOOLS", tools), patch.object(api, "JOBS", {}), patch.object(api, "SOURCE_INDEX", {}), patch.object(api, "INDEX_PATH", Path(directory) / "index.json"), \
                    patch.object(DownloadJob, "start"):
                payload = {
                    "urls": ["http://example.com/a.m3u8", "http://example.com/b.m3u8"],
                    "filename": filename,
                    "out_dir": directory,
                }
                if filenames is not None:
                    payload["filenames"] = filenames
                result = api.start_batch(payload)
        self.assertEqual(result["errors"], [])
        return [Path(job["output"]).name for job in result["jobs"]]

    def test_shared_name_gets_sequence_before_extension(self):
        self.assertEqual(self.run_batch("课程.mkv"), ["课程_01.mkv", "课程_02.mkv"])

    def test_individual_names_override_prefix(self):
        self.assertEqual(self.run_batch("课程", ["开场", "结尾.mov"]), ["开场.mp4", "结尾.mov"])

    def test_blank_individual_name_uses_prefix(self):
        self.assertEqual(self.run_batch("课程", ["开场", ""]), ["开场.mp4", "课程_02.mp4"])

    def test_blank_names_use_urls(self):
        self.assertEqual(self.run_batch(), ["a.mp4", "b.mp4"])

    def test_duplicate_and_existing_names_are_not_overwritten(self):
        self.assertEqual(self.run_batch(filenames=["视频", "视频"], existing=["视频.mp4"]),
                         ["视频_2.mp4", "视频_3.mp4"])

    def test_names_must_match_urls(self):
        with self.assertRaisesRegex(ValueError, "一一对应"):
            self.run_batch(filenames=["一个名字"])


if __name__ == "__main__":
    unittest.main()
