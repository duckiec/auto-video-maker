from __future__ import annotations

import os
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import uuid


class TestContainerImageSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.run_image_tests = os.getenv("RUN_IMAGE_TESTS", "0") == "1"
        self.image = os.getenv("IMAGE_UNDER_TEST", "local/video-factory:test")
        self.container_name = f"video-factory-smoke-{uuid.uuid4().hex[:8]}"

        if not self.run_image_tests:
            self.skipTest("Set RUN_IMAGE_TESTS=1 to run container smoke checks.")

        try:
            subprocess.run(["docker", "version"], check=True, capture_output=True, text=True)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Docker not available: {exc}")

    def tearDown(self) -> None:
        subprocess.run(
            ["docker", "rm", "-f", self.container_name],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_image_starts_and_serves_health(self) -> None:
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.container_name,
                "-p",
                "5050:5000",
                self.image,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        health_url = "http://127.0.0.1:5050/health"
        deadline = time.time() + 60
        last_error: Exception | None = None

        while time.time() < deadline:
            try:
                with urllib.request.urlopen(health_url, timeout=5) as response:
                    body = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("ok", body.lower())
                    return
            except (urllib.error.URLError, AssertionError) as exc:
                last_error = exc
                time.sleep(2)

        logs = subprocess.run(
            ["docker", "logs", self.container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        self.fail(
            f"Container health endpoint never became ready. Last error: {last_error}\nLogs:\n{logs.stdout}\n{logs.stderr}"
        )
