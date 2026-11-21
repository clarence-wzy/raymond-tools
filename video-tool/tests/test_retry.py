import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from video_downloader import api, config
from video_downloader.core import DownloadJob, JobConfig
from video_downloader.locator import Tools


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for patcher in (
            patch.object(api, "JOBS", {}), patch.object(api, "SOURCE_INDEX", {}),
            patch.object(api, "INDEX_PATH", self.root / "index.json"),
            patch.object(api, "CFG", dict(config.DEFAULTS)),
            patch.object(api, "TOOLS", Tools(Path("ffmpeg"), Path("ffprobe"), "test")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def old_job(self):
        job = DownloadJob(JobConfig("https://example.com/index.m3u8?token=private", self.root / "original.mp4", headers="Cookie: private", proxy="direct", restart=True), Path("ffmpeg"), Path("ffprobe"))
        job._set(status="failed")
        api.JOBS[job.id] = job
        return job

    def test_direct_retry_retains_identity_credentials_and_cache(self):
        old = self.old_job()
        cache = self.root / "original.mp4.parts"
        cache.mkdir()
        marker = cache / "part_000.ts"
        marker.write_bytes(b"keep")
        with patch.object(DownloadJob, "start"), patch.object(DownloadJob, "_log"):
            result = api.retry({"id": old.id})
        new = api.JOBS[old.id]
        self.assertEqual(result["job"]["id"], old.id)
        self.assertFalse(new.cfg.restart)
        self.assertEqual(new.cfg.headers, old.cfg.headers)
        self.assertEqual(new.cfg.proxy, old.cfg.proxy)
        self.assertEqual(new.cfg.output.resolve(), old.cfg.output.resolve())
        self.assertEqual(marker.read_bytes(), b"keep")
        self.assertNotIn("token=private", api.INDEX_PATH.read_text())
        self.assertNotIn("Cookie: private", api.INDEX_PATH.read_text())
        self.assertEqual(len(api.JOBS), 1)

    def test_active_retry_is_idempotent(self):
        old = self.old_job()
        old._thread = Mock()
        old._thread.is_alive.return_value = True
        with patch.object(DownloadJob, "start") as start:
            api.retry({"id": old.id})
            api.retry({"id": old.id})
        start.assert_not_called()
        self.assertIs(api.JOBS[old.id], old)

    def test_same_source_different_name_reuses_original_task(self):
        old = self.old_job()
        with patch.object(DownloadJob, "start"), patch.object(DownloadJob, "_log"):
            new = api._create_job({"out_dir": str(self.root), "proxy": "direct"}, old.cfg.url, dedupe=True, filename="different.mp4")
        self.assertEqual(new.id, old.id)
        self.assertEqual(new.cfg.output.resolve(), old.cfg.output.resolve())
        self.assertFalse((self.root / "different.mp4.parts").exists())

    def test_source_index_reuses_cache_after_task_records_are_gone(self):
        old = self.old_job()
        (self.root / "original.mp4.parts").mkdir()
        api._remember(old)
        api.JOBS.clear()
        with patch.object(DownloadJob, "start"):
            new = api._create_job({"out_dir": str(self.root)}, old.cfg.url, dedupe=True, filename="another-name.mp4")
        self.assertEqual(new.cfg.output.resolve(), old.cfg.output.resolve())

    def test_completed_source_returns_existing_file_without_download(self):
        old = self.old_job()
        old.cfg.output.write_bytes(b"completed output")
        old._set(status="done", total_time=10, percent=100)
        api._remember(old)
        api.JOBS.clear()
        with patch.object(DownloadJob, "start") as start:
            new = api._create_job({"out_dir": str(self.root)}, old.cfg.url, dedupe=True, filename="copy.mp4")
        start.assert_not_called()
        self.assertEqual(new.progress.status, "done")
        self.assertEqual(new.cfg.output.resolve(), old.cfg.output.resolve())

    def test_unchanged_completed_task_is_reused_in_memory(self):
        old = self.old_job()
        old.cfg.output.write_bytes(b"completed output")
        old._set(status="done")
        api._remember(old)
        with patch.object(DownloadJob, "start") as start:
            new = api._create_job({"out_dir": str(self.root)}, old.cfg.url, dedupe=True)
        self.assertIs(new, old)
        start.assert_not_called()

    def test_modified_completed_files_are_preserved_and_not_reused(self):
        for reloaded in (False, True):
            for change in ("truncated", "same-size", "missing"):
                with self.subTest(reloaded=reloaded, change=change):
                    api.JOBS.clear()
                    api.SOURCE_INDEX.clear()
                    old = self.old_job()
                    old.cfg.output.write_bytes(b"original")
                    old._set(status="done")
                    api._remember(old)
                    if change == "missing":
                        old.cfg.output.unlink()
                    else:
                        before = old.cfg.output.stat()
                        old.cfg.output.write_bytes(b"" if change == "truncated" else b"modified")
                        os.utime(old.cfg.output, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
                    if reloaded:
                        api.JOBS.clear()
                    with patch.object(DownloadJob, "start") as start:
                        new = api._create_job({"out_dir": str(self.root)}, old.cfg.url, dedupe=False, filename=old.cfg.output.name)
                    self.assertNotEqual(new.progress.status, "done")
                    start.assert_called_once()
                    if change != "missing":
                        self.assertNotEqual(new.cfg.output, old.cfg.output)
                        self.assertEqual(old.cfg.output.read_bytes(), b"" if change == "truncated" else b"modified")

    def test_retry_rejects_changed_completed_file(self):
        old = self.old_job()
        old.cfg.output.write_bytes(b"original")
        old._set(status="done")
        api._remember(old)
        old.cfg.output.write_bytes(b"")
        with patch.object(DownloadJob, "start") as start:
            with self.assertRaisesRegex(ValueError, "已更改或丢失"):
                api.retry({"id": old.id})
        start.assert_not_called()
        self.assertEqual(old.cfg.output.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
