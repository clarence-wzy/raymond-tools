"""Run portable tests and expose failures in GitHub check annotations."""

from __future__ import annotations

import os
import unittest

TESTS = (
    "tests.test_core",
    "tests.test_retry",
    "tests.test_folder_picker",
    "tests.test_security",
    "tests.test_repository",
    "tests.test_download.ReliabilityTest",
    "tests.test_download.QueueTest",
    "tests.test_download.PlaylistTest",
)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromNames(TESTS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for test, traceback in result.failures + result.errors:
            message = f"{test.id()}\n{traceback}"
            escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Python test failure::{escaped}", flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
