from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("git"), "git required to verify repository ignore rules")
class RepositoryIgnoreTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[2]
        (self.root / "video-tool").mkdir()
        shutil.copy2(source / ".gitignore", self.root / ".gitignore")
        shutil.copy2(source / "video-tool/.gitignore", self.root / "video-tool/.gitignore")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)

    def ignored(self, paths):
        result = subprocess.run(
            ["git", "-c", f"core.excludesFile={os.devnull}", "check-ignore", "--no-index", "--stdin", "-z"],
            cwd=self.root, input="\0".join(paths) + "\0", capture_output=True, text=True,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return set(filter(None, result.stdout.split("\0")))

    def test_local_state_and_atomic_write_residue_stay_out_of_git(self):
        paths = [
            "video-tool/config.json", "video-tool/config.random.json",
            "video-tool/.task-index.json", "video-tool/.task-index.random",
            "video-tool/example.mp4.parts/hls-state.json", "video-tool/example.mp4.parts/key_0000.key",
            "video-tool/downloads/example.mp4", "video-tool/example.mp4", "video-tool/debug.log",
            "video-tool/.env", "video-tool/.env.local", ".env.local", ".DS_Store",
        ]
        self.assertEqual(self.ignored(paths), set(paths))

    def test_code_examples_and_public_docs_remain_visible_to_git(self):
        paths = [
            "LICENSE", "README.md", ".github/workflows/video-tool.yml",
            "video-tool/config.example.json", "video-tool/.env.example", ".env.example",
            "video-tool/app.py", "video-tool/video_downloader/core.py", "video-tool/tests/test_security.py",
        ]
        self.assertEqual(self.ignored(paths), set())


if __name__ == "__main__":
    unittest.main()
