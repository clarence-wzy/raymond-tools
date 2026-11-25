from __future__ import annotations

import functools
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from video_downloader import hls
from video_downloader import api, config
from video_downloader.core import Cancelled, DownloadJob, IncompleteDownload, JobConfig
from video_downloader.scheduler import DownloadScheduler


def make_job(output: Path, **kwargs):
    job = DownloadJob(JobConfig("https://example.com/index.m3u8", output, **kwargs), Path(shutil.which("ffmpeg") or "ffmpeg"), Path(shutil.which("ffprobe") or "ffprobe"))
    job._log = lambda message: None
    return job


class ReliabilityTest(unittest.TestCase):
    def test_unchanged_progress_does_not_reset_watchdog(self):
        job = make_job(Path("unused.mp4"))
        with patch("video_downloader.core.time.monotonic", return_value=10):
            job._set(size=100, out_time=1)
        with patch("video_downloader.core.time.monotonic", return_value=20):
            job._set(size=100, out_time=1)
        self.assertEqual(job.progress.updated, 10)
        with patch("video_downloader.core.time.monotonic", return_value=21):
            job._set(size=200)
        self.assertEqual(job.progress.updated, 21)
        self.assertGreater(job.progress.bytes_per_second, 0)

    def test_repeated_ffmpeg_heartbeats_still_time_out(self):
        job = make_job(Path("unused.mp4"), stall_timeout=0.25)
        script = 'import time\nwhile True:\n print("out_time_us=1000000\\ntotal_size=100\\nprogress=continue", flush=True)\n time.sleep(0.03)'
        started = time.monotonic()
        rc = job._run_ffmpeg([sys.executable, "-u", "-c", script], 0, 100)
        self.assertNotEqual(rc, 0)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(job.progress.percent, 1)

    def test_ffmpeg_end_does_not_mean_video_complete(self):
        job = make_job(Path("unused.mp4"))
        script = 'print("out_time_us=10000000\\ntotal_size=100\\nprogress=end"); raise SystemExit(1)'
        self.assertEqual(job._run_ffmpeg([sys.executable, "-c", script], 2, 100), 1)
        self.assertEqual(job.progress.percent, 8)
        self.assertEqual(job.progress.out_time, 8)

    def test_probe_forwards_headers_and_timeout(self):
        job = make_job(Path("unused.mp4"), headers="User-Agent: VideoToolsTest")
        result = subprocess.CompletedProcess([], 0, '{"format":{"duration":"30","start_time":"nan"}}', "")
        with patch.object(job, "_capture", return_value=result) as capture:
            self.assertEqual(job._probe(job.cfg.url), (0, 30, ""))
        command = capture.call_args.args[0]
        self.assertIn("-rw_timeout", command)
        self.assertIn("user-agent: VideoToolsTest\r\n", command[command.index("-headers") + 1])

    def test_short_output_preserves_existing_file_and_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "video.mp4"
            output.write_bytes(b"existing")
            parts = Path(directory) / "video.mp4.parts"
            parts.mkdir()
            marker = parts / "part_000.ts"
            marker.write_bytes(b"keep")
            job = make_job(output)
            def merge(paths, target):
                target.write_bytes(b"short output")
            with patch.object(job, "_merge", side_effect=merge), patch.object(job, "_probe", return_value=(0, 3, "mp4")):
                with self.assertRaises(IncompleteDownload):
                    job._finish([marker], 100, parts)
            self.assertEqual(output.read_bytes(), b"existing")
            self.assertTrue(marker.exists())
            self.assertEqual(list(Path(directory).glob("*.tmp.mp4")), [])

    def test_cancel_during_capture_stops_process(self):
        job = make_job(Path("unused.mp4"))
        spawned = threading.Event()
        errors = []
        original = job._spawn
        def spawn(*args, **kwargs):
            proc = original(*args, **kwargs)
            spawned.set()
            return proc
        def capture():
            try:
                job._capture([sys.executable, "-c", "import time; time.sleep(30)"], 40)
            except BaseException as error:
                errors.append(error)
        with patch.object(job, "_spawn", side_effect=spawn):
            thread = threading.Thread(target=capture)
            thread.start()
            self.assertTrue(spawned.wait(3))
            job.cancel()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], Cancelled)
        self.assertIsNone(job._proc)

    def test_cancel_during_merge_cannot_publish_output(self):
        with tempfile.TemporaryDirectory() as directory:
            job = make_job(Path(directory) / "video.mp4")
            part = Path(directory) / "part.ts"
            part.write_bytes(b"keep")
            def merge(paths, output):
                output.write_bytes(b"temporary")
                job.cancel()
            with patch.object(job, "_merge", side_effect=merge), patch.object(job, "_probe", return_value=(0, 20, "mp4")):
                with self.assertRaises(Cancelled):
                    job._finish([part], 20, Path(directory))
            self.assertFalse(job.cfg.output.exists())
            self.assertTrue(part.exists())

    def test_file_created_during_merge_is_only_replaced_on_explicit_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for restart in (False, True):
                with self.subTest(restart=restart):
                    output = root / f"video-{restart}.mp4"
                    parts = root / (output.name + ".parts")
                    parts.mkdir()
                    part = parts / "part.ts"
                    part.write_bytes(b"keep")
                    job = make_job(output, restart=restart, keep_parts=True)

                    def merge(paths, export):
                        export.write_bytes(b"download")
                        output.write_bytes(b"another file")

                    with patch.object(job, "_merge", side_effect=merge), patch.object(job, "_probe", return_value=(0, 20, "mp4")):
                        if restart:
                            job._finish([part], 20, parts)
                        else:
                            with self.assertRaisesRegex(IncompleteDownload, "输出文件已存在"):
                                job._finish([part], 20, parts)
                    self.assertEqual(output.read_bytes(), b"download" if restart else b"another file")
                    self.assertEqual(part.read_bytes(), b"keep")
                    self.assertEqual(list(root.glob("*.tmp.mp4")), [])

    def test_tolerance_is_opt_in(self):
        strict = make_job(Path("unused.mp4"))
        tolerant = make_job(Path("unused.mp4"), strict_errors=False)
        self.assertIn("-xerror", strict._build_cmd(Path("part.ts"), 0, True))
        self.assertNotIn("-xerror", tolerant._build_cmd(Path("part.ts"), 0, True))


class QueueTest(unittest.TestCase):
    def test_limit_queue_cancellation_and_reconfigure(self):
        queue = DownloadScheduler(1)
        self.assertTrue(queue.acquire("a", threading.Event()))
        cancelled = threading.Event()
        results = []
        thread = threading.Thread(target=lambda: results.append(queue.acquire("b", cancelled)))
        thread.start()
        for _ in range(100):
            if queue.position("b") == 1:
                break
            time.sleep(0.005)
        self.assertEqual(queue.position("b"), 1)
        cancelled.set()
        thread.join(1)
        self.assertEqual(results, [False])
        self.assertEqual(queue.active, {"a"})
        queue.configure(2)
        self.assertTrue(queue.acquire("c", threading.Event()))
        queue.release("a")
        queue.release("c")
        self.assertEqual(queue.active, set())

    def test_settings_persist_and_reconfigure_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"), patch.object(api, "CFG", dict(config.DEFAULTS)), patch.object(api.SCHEDULER, "configure") as configure:
                api.settings({"max_concurrent": 3, "hls_workers": 5, "strict_errors": False})
                self.assertEqual(config.load()["max_concurrent"], 3)
                self.assertEqual(config.load()["hls_workers"], 5)
                self.assertFalse(config.load()["strict_errors"])
                configure.assert_called_once_with(3)

    def test_invalid_settings_and_save_failure_do_not_mutate_config(self):
        with patch.object(api, "CFG", dict(config.DEFAULTS)):
            for payload in ({"max_concurrent": 0}, {"hls_workers": 9}, {"strict_errors": "false"}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    api.settings(payload)
            with patch.object(config, "save", side_effect=PermissionError), self.assertRaises(ValueError):
                api.settings({"max_concurrent": 3})
            self.assertEqual(api.CFG["max_concurrent"], 2)


class PlaylistTest(unittest.TestCase):
    def test_aes_implicit_iv_and_discontinuity(self):
        text = '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n#EXTINF:2,\na.ts\n#EXT-X-DISCONTINUITY\n#EXTINF:2,\nb.ts\n#EXT-X-ENDLIST'
        segments = hls.parse(text, "https://example.com/f/index.m3u8")
        self.assertEqual(segments[0].iv, "0x00000000000000000000000000000007")
        self.assertEqual(segments[1].iv, "0x00000000000000000000000000000008")
        self.assertEqual(segments[0].key, "https://example.com/f/key.bin")
        self.assertTrue(segments[1].discontinuity)

    def test_unsupported_playlists_fall_back(self):
        for text in ("#EXTM3U\n#EXTINF:2,\na.ts", "#EXTM3U\n#EXT-X-MAP:URI=init.mp4\n#EXTINF:2,\na.ts\n#EXT-X-ENDLIST"):
            with self.subTest(text=text), self.assertRaises(hls.UnsupportedPlaylist):
                hls.parse(text, "https://example.com/")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg/ffprobe required")
class HlsIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.counts = Counter()
        cls.fail_once = set()
        cls.guard = threading.Lock()
        cls.root.joinpath("key.key").write_bytes(bytes(range(16)))
        keyinfo = cls.root / "keyinfo"
        keyinfo.write_text("key.key\n" + str(cls.root / "key.key") + "\n")
        for name, encrypted in (("plain", False), ("encrypted", True)):
            command = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10", "-t", "8", "-c:v", "libx264", "-g", "10", "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", "-hls_segment_filename", str(cls.root / (name + "%02d.ts"))]
            if encrypted:
                command += ["-hls_key_info_file", str(keyinfo)]
            subprocess.run(command + [str(cls.root / (name + ".m3u8"))], check=True, capture_output=True, timeout=15)
        class Handler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                if self.headers.get("User-Agent") != "VideoToolsTest":
                    self.send_error(403)
                    return
                with cls.guard:
                    cls.counts[self.path] += 1
                    failing = self.path in cls.fail_once
                    cls.fail_once.discard(self.path)
                if failing:
                    self.send_error(503)
                else:
                    super().do_GET()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(cls.root)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def run_job(self, directory, name, *, query="", **kwargs):
        job = make_job(Path(directory) / (name + ".mp4"), keep_parts=True, headers="User-Agent: VideoToolsTest", proxy="direct", **kwargs)
        job.cfg.url = f"http://127.0.0.1:{self.server.server_port}/{name}.m3u8{query}"
        job.start()
        job._thread.join(25)
        if job.is_running():
            job.cancel()
            self.fail("download did not finish")
        self.assertEqual(job.progress.status, "done", job.progress.message)
        self.assertEqual(job.progress.percent, 100)
        self.assertGreater(job.cfg.output.stat().st_size, 1000)
        return job

    def test_plain_download_and_checkpoint_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.run_job(directory, "plain")
            first.cfg.output.rename(Path(directory) / "first.mp4")
            before = {key: value for key, value in self.counts.items() if key.startswith("/plain") and key.endswith(".ts")}
            self.run_job(directory, "plain")
            after = {key: value for key, value in self.counts.items() if key.startswith("/plain") and key.endswith(".ts")}
            self.assertEqual(before, after)
            state = json.loads((Path(directory) / "plain.mp4.parts" / "hls-state.json").read_text())
            self.assertEqual(len(state["completed"]), 4)

    def test_aes_download_and_single_fragment_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.guard:
                before = self.counts["/encrypted02.ts"]
                self.fail_once.add("/encrypted02.ts")
            job = self.run_job(directory, "encrypted", max_retries=2)
            self.assertEqual(self.counts["/encrypted02.ts"] - before, 2)
            self.assertEqual(job.progress.retries, 1)

    def test_legacy_ffmpeg_download_still_works(self):
        with tempfile.TemporaryDirectory() as directory:
            self.run_job(directory, "plain", native_hls=False)

    def test_complete_cache_can_merge_without_accessing_the_source(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.run_job(directory, "plain")
            first.cfg.output.rename(Path(directory) / "first.mp4")
            with patch.object(hls, "download", side_effect=AssertionError("must not access the source")):
                self.run_job(directory, "plain")

    def test_renewed_hls_address_checks_playlist_and_reuses_matching_segments(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.run_job(directory, "plain")
            first.cfg.output.rename(Path(directory) / "first.mp4")
            playlist = "/plain.m3u8?token=renewed"
            fragments = [f"/plain{index:02d}.ts" for index in range(4)]
            before = {path: self.counts[path] for path in [playlist, *fragments]}
            self.run_job(directory, "plain", query="?token=renewed", native_hls=False)
            self.assertEqual(self.counts[playlist], before[playlist] + 1)
            self.assertEqual({path: self.counts[path] for path in fragments}, {path: before[path] for path in fragments})

    def test_failed_download_keeps_successful_peers_for_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.guard:
                self.fail_once.add("/plain02.ts")
            job = make_job(Path(directory) / "plain.mp4", keep_parts=True, headers="User-Agent: VideoToolsTest", proxy="direct", hls_workers=1, max_retries=1)
            job.cfg.url = f"http://127.0.0.1:{self.server.server_port}/plain.m3u8"
            job.start()
            job._thread.join(10)
            self.assertEqual(job.progress.status, "failed")
            checkpoint = Path(directory) / "plain.mp4.parts" / "hls-state.json"
            state = json.loads(checkpoint.read_text())
            self.assertIn("0", state["completed"])
            self.assertIn("1", state["completed"])
            before = [self.counts["/plain00.ts"], self.counts["/plain01.ts"]]
            self.run_job(directory, "plain", hls_workers=1)
            self.assertEqual(before, [self.counts["/plain00.ts"], self.counts["/plain01.ts"]])


if __name__ == "__main__":
    unittest.main()
