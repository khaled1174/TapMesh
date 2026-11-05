"""OCR boundary tests; controlled executables never operate a device."""
import asyncio
import errno
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from PIL import Image

from agent.safety import BridgeError
from server import ocr


HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
WORDS = (
    "5\t1\t1\t1\t1\t1\t20\t40\t60\t20\t96\tShow\n"
    "5\t1\t1\t1\t1\t2\t90\t40\t80\t20\t88\talert\n"
    "5\t1\t2\t1\t1\t1\t30\t100\t100\t30\t90\tمرحبا\n"
)


def image_bytes(kind="PNG", size=(400, 800)):
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, format=kind)
    return output.getvalue()


def oversized_png_header():
    # A valid IHDR is sufficient for Pillow to identify dimensions before load.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4000, 2001, 8, 2, 0, 0, 0)) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")


class OCRTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tapmesh-ocr-test-")
        self.root = Path(self.temp.name)
        self.record = self.root / "record.json"
        self.executable = self.root / "tesseract-fixture"
        self.patches = [
            patch.dict(os.environ, {"TAPMESH_TESSERACT": str(self.executable)}),
            patch.object(ocr, "_slots", asyncio.Semaphore(1)),
            patch.object(ocr, "_admitted", 0),
        ]
        for item in self.patches:
            item.start()
        self.write_executable("print(" + repr(HEADER + WORDS) + ", end='')")

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_executable(self, behavior):
        script = (
            "#!" + sys.executable + "\n"
            "import json, os, pathlib, signal, subprocess, sys, time\n"
            "image = pathlib.Path(sys.argv[1])\n"
            "record = pathlib.Path(" + repr(str(self.record)) + ")\n"
            "data = {'pid': os.getpid(), 'image': str(image), 'directory_mode': image.parent.stat().st_mode & 0o777, 'file_mode': image.stat().st_mode & 0o777, 'argv': sys.argv[2:], 'omp': os.environ.get('OMP_THREAD_LIMIT')}\n"
            "record.write_text(json.dumps(data))\n"
            + behavior + "\n"
        )
        self.executable.write_text(script)
        self.executable.chmod(0o700)

    async def recognize(self, data=None, **kwargs):
        return await ocr.recognize(image_bytes() if data is None else data, logical_size=kwargs.pop("logical_size", {"width": 200, "height": 400}), **kwargs)

    async def expect_error(self, code, data=None, **kwargs):
        with self.assertRaises(BridgeError) as caught:
            await self.recognize(data, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.side_effect_state, "not_sent")
        return caught.exception

    async def wait_for_record(self):
        async with asyncio.timeout(3):
            while True:
                try:
                    record = json.loads(self.record.read_text())
                    if "child_pid" in record:
                        return record
                except (OSError, json.JSONDecodeError):
                    pass
                await asyncio.sleep(0.01)

    def assert_process_gone(self, pid):
        # A terminated child may briefly be a zombie awaiting its OS reaper.
        if sys.platform.startswith('linux'):
            try:
                state = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except FileNotFoundError:
                return
            self.assertEqual(state, 'Z')
            return
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False)
        self.assertTrue(result.returncode != 0 or result.stdout.strip().startswith("Z"), result.stdout)

    async def test_maps_word_union_to_logical_points_and_preserves_unicode(self):
        result = await self.recognize()
        self.assertEqual(result["source"], "ocr")
        self.assertEqual(result["logical_size"], {"width": 200, "height": 400})
        self.assertEqual(result["image_size"], {"width": 400, "height": 800})
        self.assertEqual(len(result["elements"]), 2)
        first, arabic = result["elements"]
        self.assertEqual(first, {"label": "Show alert", "value": None, "role": "text", "bounds": {"x": 10.0, "y": 20.0, "width": 75.0, "height": 10.0}, "source": "ocr", "confidence": 0.92})
        self.assertEqual(arabic["label"], "مرحبا")
        self.assertNotIn("ref", first)
        self.assertNotIn("id", first)
        record = json.loads(self.record.read_text())
        self.assertEqual(record["argv"], ["stdout", "-l", "eng+ara", "--psm", "11", "tsv"])
        self.assertEqual(record["omp"], "1")
        self.assertEqual(record["directory_mode"], 0o700)
        self.assertEqual(record["file_mode"], 0o600)
        self.assertFalse(Path(record["image"]).parent.exists())

    async def test_accepts_verified_jpeg(self):
        result = await self.recognize(image_bytes("JPEG"), languages="eng")
        self.assertEqual(result["elements"][0]["label"], "Show alert")

    async def test_valid_empty_ocr_is_empty_without_fabricated_elements(self):
        self.write_executable("print(" + repr(HEADER) + ", end='')")
        self.assertEqual((await self.recognize())["elements"], [])

    async def test_rejects_malformed_truncated_and_wrong_format_images(self):
        for data in (b"", b"not an image", image_bytes()[:-20], image_bytes("GIF"), b"\xff\xd8\xffnot-jpeg"):
            with self.subTest(data=data[:16]):
                await self.expect_error("invalid_arguments", data)
        self.assertFalse(self.record.exists())

    async def test_rejects_encoded_size_and_pixel_bomb_before_load(self):
        await self.expect_error("invalid_arguments", b"x" * (8 * 1024 * 1024 + 1))
        with patch.object(Image.Image, "load", side_effect=AssertionError("must not load oversized pixels")):
            await self.expect_error("invalid_arguments", oversized_png_header())
        self.assertFalse(self.record.exists())

    async def test_rejects_untrusted_logical_bounds_and_aspect_mismatch(self):
        for logical in ({"width": float("nan"), "height": 400}, {"width": 200, "height": float("inf")}, {"width": True, "height": 400}, {"width": 0, "height": 400}, {"width": -1, "height": 400}, {"width": 200, "height": 400, "x": 0}, {"width": 200, "height": 200}, {"width": 100000, "height": 200000}, None):
            with self.subTest(logical=logical):
                await self.expect_error("invalid_arguments", logical_size=logical)
        self.assertFalse(self.record.exists())

    async def test_rejects_caller_language_flags(self):
        for languages in ("eng --psm 0", "eng+fra", "ara+eng", ["eng"], None):
            with self.subTest(languages=languages):
                await self.expect_error("invalid_arguments", languages=languages)
        self.assertFalse(self.record.exists())

    async def test_missing_or_relative_executable_is_prerequisite(self):
        for executable in (str(self.root / "missing"), "tesseract"):
            with patch.dict(os.environ, {"TAPMESH_TESSERACT": executable}):
                await self.expect_error("prerequisite_required")

    async def test_backend_failure_does_not_expose_stderr(self):
        self.write_executable("sys.stderr.write('secret screenshot content'); sys.exit(2)")
        error = await self.expect_error("backend_failure")
        self.assertNotIn("secret", str(error.public()))
        self.assertEqual(error.details, {"exit_code": 2})

    async def test_missing_language_model_is_prerequisite(self):
        self.write_executable("sys.stderr.write('Error opening data file /private/eng.traineddata'); sys.exit(1)")
        await self.expect_error("prerequisite_required")

    async def test_private_storage_errors_are_structured(self):
        with patch.object(ocr.tempfile, "TemporaryDirectory", side_effect=OSError(errno.ENOSPC, "private-path")):
            error = await self.expect_error("insufficient_storage")
        self.assertNotIn("private-path", str(error.public()))

    async def test_parser_rejects_invalid_geometry_confidence_and_header(self):
        bad_rows = [
            HEADER + WORDS.replace("\t20\t40\t60\t20\t", "\t-1\t40\t60\t20\t", 1),
            HEADER + WORDS.replace("\t20\t40\t60\t20\t", "\t390\t40\t60\t20\t", 1),
            HEADER + WORDS.replace("\t96\t", "\tnan\t", 1),
            HEADER + WORDS.replace("\t96\t", "\t101\t", 1),
            HEADER + WORDS.replace("\t60\t20\t96\t", "\t0\t20\t96\t", 1),
            "unexpected\n" + WORDS,
            HEADER + "5\t1\tmissing\n",
        ]
        for output in bad_rows:
            with self.subTest(output=output):
                self.write_executable("print(" + repr(output) + ", end='')")
                await self.expect_error("backend_failure")

    async def test_parser_rejects_invalid_utf8(self):
        self.write_executable("sys.stdout.buffer.write(b'\\xff')")
        await self.expect_error("backend_failure")

    async def test_stdout_and_stderr_are_bounded(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream), patch.object(ocr, "_MAX_STDOUT_BYTES", 1024), patch.object(ocr, "_MAX_STDERR_BYTES", 1024):
                self.write_executable("sys." + stream + ".write('x' * 65536); sys." + stream + ".flush(); time.sleep(20)")
                await self.expect_error("backend_failure")
                record = json.loads(self.record.read_text())
                self.assert_process_gone(record["pid"])
                self.assertFalse(Path(record["image"]).parent.exists())

    def write_sleeping_tree(self):
        self.write_executable(
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "def stop(signum, frame):\n"
            "    child.wait(timeout=2)\n"
            "    sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "data['child_pid'] = child.pid\n"
            "record.write_text(json.dumps(data))\n"
            "time.sleep(30)"
        )

    async def test_timeout_terminates_owned_process_group_and_removes_tempfiles(self):
        self.write_sleeping_tree()
        with patch.object(ocr, "_PROCESS_TIMEOUT_SECONDS", 2.0):
            await self.expect_error("deadline_exceeded")
        record = json.loads(self.record.read_text())
        self.assert_process_gone(record["pid"])
        self.assert_process_gone(record["child_pid"])
        self.assertFalse(Path(record["image"]).parent.exists())

    async def test_cancellation_terminates_owned_process_group_and_removes_tempfiles(self):
        self.write_sleeping_tree()
        task = asyncio.create_task(self.recognize())
        try:
            record = await self.wait_for_record()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assert_process_gone(record["pid"])
            self.assert_process_gone(record["child_pid"])
            self.assertFalse(Path(record["image"]).parent.exists())
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_repeated_cancellation_kills_term_resistant_process_group(self):
        self.write_executable(
            "child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "data['child_pid'] = child.pid\n"
            "record.write_text(json.dumps(data))\n"
            "time.sleep(30)"
        )
        task = asyncio.create_task(self.recognize())
        try:
            record = await self.wait_for_record()
            task.cancel()
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assert_process_gone(record["pid"])
            self.assert_process_gone(record["child_pid"])
            self.assertFalse(Path(record["image"]).parent.exists())
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_success_cleans_descendant_after_leader_has_exited(self):
        self.write_executable(
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "data['child_pid'] = child.pid\n"
            "record.write_text(json.dumps(data))\n"
            "print(" + repr(HEADER) + ", end='')"
        )
        self.assertEqual((await self.recognize())["elements"], [])
        record = json.loads(self.record.read_text())
        self.assert_process_gone(record["pid"])
        self.assert_process_gone(record["child_pid"])
        self.assertFalse(Path(record["image"]).parent.exists())

    async def test_queue_has_one_active_process_and_releases_cancelled_waiter(self):
        self.write_sleeping_tree()
        active = asyncio.create_task(self.recognize())
        waiting = None
        try:
            await self.wait_for_record()
            with patch.object(ocr, "_QUEUE_TIMEOUT_SECONDS", 0.05):
                await self.expect_error("deadline_exceeded")
            waiting = asyncio.create_task(self.recognize())
            await asyncio.sleep(0.01)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
            self.assertEqual(ocr._admitted, 1)
            self.assertEqual(len(list(self.root.glob("record.json"))), 1)
        finally:
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        self.assertEqual(ocr._admitted, 0)
        self.write_executable("print(" + repr(HEADER) + ", end='')")
        self.assertEqual((await self.recognize())["elements"], [])

    async def test_queue_rejects_excess_requests_immediately(self):
        with patch.object(ocr, "_admitted", ocr._MAX_ADMITTED):
            error = await self.expect_error("deadline_exceeded")
        self.assertTrue(error.retryable)
        self.assertFalse(self.record.exists())

    async def test_parser_limits_result_line_count(self):
        rows = "".join("5\t1\t1\t1\t" + str(i + 1) + "\t1\t20\t40\t60\t20\t96\ttext\n" for i in range(3))
        self.write_executable("print(" + repr(HEADER + rows) + ", end='')")
        with patch.object(ocr, "_MAX_LINES", 2):
            await self.expect_error("backend_failure")


if __name__ == "__main__":
    unittest.main()
