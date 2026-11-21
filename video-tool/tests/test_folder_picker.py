from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_downloader import api, config, folder_picker


class FolderPickerTest(unittest.TestCase):
    def test_mac_passes_path_as_argument_and_returns_selected_directory(self):
        with tempfile.TemporaryDirectory(prefix='视频 " $ ') as directory:
            result = subprocess.CompletedProcess([], 0, directory + "/\n", "")
            with patch.object(folder_picker.sys, "platform", "darwin"), \
                    patch.object(folder_picker.subprocess, "run", return_value=result) as run:
                self.assertEqual(folder_picker.choose(directory), directory)
            command = run.call_args.args[0]
            self.assertEqual(command[0], "osascript")
            self.assertEqual(command[-1], directory)
            self.assertNotIn(directory, command[2])

    def test_cancel_returns_none(self):
        result = subprocess.CompletedProcess([], 0, "\n", "")
        with patch.object(folder_picker.subprocess, "run", return_value=result):
            self.assertIsNone(folder_picker.choose(str(Path.home())))

    def test_failure_releases_dialog_lock(self):
        with patch.object(folder_picker.subprocess, "run", side_effect=OSError):
            with self.assertRaisesRegex(ValueError, "无法打开"):
                folder_picker.choose(str(Path.home()))
        self.assertFalse(folder_picker._LOCK.locked())

    def test_rejects_duplicate_dialog(self):
        folder_picker._LOCK.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "已打开"):
                folder_picker.choose(str(Path.home()))
        finally:
            folder_picker._LOCK.release()

    def test_nonexistent_selected_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.CompletedProcess([], 0, str(Path(directory) / "missing"), "")
            with patch.object(folder_picker.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(ValueError, "不存在"):
                    folder_picker.choose(directory)

    def test_selection_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"), \
                    patch.object(api, "CFG", dict(config.DEFAULTS)), \
                    patch.object(folder_picker, "choose", return_value=directory):
                result = api.select_folder({"path": directory})
                self.assertEqual(result["path"], directory)
                self.assertEqual(api.CFG["output_dir"], directory)
                self.assertEqual(config.load()["output_dir"], directory)

    def test_cancel_does_not_save(self):
        with patch.object(folder_picker, "choose", return_value=None), \
                patch.object(config, "save") as save:
            self.assertTrue(api.select_folder({})["cancelled"])
            save.assert_not_called()

    def test_save_failure_keeps_previous_directory(self):
        with patch.object(api, "CFG", dict(config.DEFAULTS, output_dir="original")), \
                patch.object(folder_picker, "choose", return_value=str(Path.home())), \
                patch.object(config, "save", side_effect=PermissionError):
            with self.assertRaisesRegex(ValueError, "无法保存"):
                api.select_folder({})
            self.assertEqual(api.CFG["output_dir"], "original")


if __name__ == "__main__":
    unittest.main()
