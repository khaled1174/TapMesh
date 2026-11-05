"""NAS-side OCR with bounded image decoding and owned Tesseract processes.

Only the service's trusted TAPMESH_TESSERACT environment setting selects the
executable. Request data cannot select programs, flags, paths or Tesseract
configuration. This module issues text boxes, never accessibility identities.
"""
import asyncio
import errno
import io
import math
import os
from pathlib import Path
import signal
import tempfile
import warnings

from PIL import Image, UnidentifiedImageError

from agent.safety import BridgeError


_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_PIXELS = 8_000_000
_MAX_LOGICAL_DIMENSION = 16_384
_MAX_STDOUT_BYTES = 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024
_MAX_ROWS = 12_000
_MAX_WORDS = 4_000
_MAX_LINES = 1_000
_MAX_LABEL_CHARACTERS = 4_096
_MAX_TEXT_CHARACTERS = 128_000
_PROCESS_TIMEOUT_SECONDS = 8.0
_QUEUE_TIMEOUT_SECONDS = 2.0
_TERMINATE_GRACE_SECONDS = 0.5
_MAX_ADMITTED = 3  # One running request and at most two waiting requests.
_slots = asyncio.Semaphore(1)
_admitted = 0
_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _invalid(message):
    return BridgeError("invalid_arguments", message)


def _backend(message):
    return BridgeError("backend_failure", message)


def _validate_request(image_data, logical_size, languages):
    if not isinstance(image_data, bytes) or not image_data or len(image_data) > _MAX_IMAGE_BYTES:
        raise _invalid("OCR requires image bytes between 1 byte and 8 MiB")
    if not isinstance(languages, str) or languages not in {"eng", "ara", "eng+ara"}:
        raise _invalid("OCR languages must be eng, ara or eng+ara")
    if not isinstance(logical_size, dict) or set(logical_size) != {"width", "height"}:
        raise _invalid("OCR logical_size requires only width and height")
    result = {}
    for key in ("width", "height"):
        value = logical_size[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _invalid("OCR logical dimensions must be finite positive numbers")
        try:
            valid = math.isfinite(value) and 0 < value <= _MAX_LOGICAL_DIMENSION
        except (OverflowError, ValueError):
            valid = False
        if not valid:
            raise _invalid("OCR logical dimensions must be finite and at most 16384 points")
        result[key] = value
    return result


def _write_verified_image(image_data, logical_size, path):
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        expected_format = "PNG"
    elif image_data.startswith(b"\xff\xd8\xff"):
        expected_format = "JPEG"
    else:
        raise _invalid("OCR accepts verified PNG or JPEG images only")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image_data), formats=("PNG", "JPEG")) as image:
                width, height = image.size
                if image.format != expected_format:
                    raise _invalid("Image signature and decoded format do not match")
                if width <= 0 or height <= 0 or width * height > _MAX_PIXELS:
                    raise _invalid("OCR images must contain at most 8 million pixels")
                # Permit at most two pixels of independent screenshot rounding.
                expected_height = width * logical_size["height"] / logical_size["width"]
                if abs(expected_height - height) > 2:
                    raise _invalid("OCR image and logical dimensions have different aspect ratios")
                image.verify()
            # verify() checks the encoded container; reopen and decode only after
            # dimensions are bounded, then discard metadata and normalize to RGB.
            with Image.open(io.BytesIO(image_data), formats=("PNG", "JPEG")) as image:
                image.load()
                with image.convert("RGB") as normalized:
                    try:
                        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        with os.fdopen(descriptor, "wb") as output:
                            normalized.save(output, format="PNG")
                    except OSError as exc:
                        raise _storage_error(exc) from exc
        return width, height
    except BridgeError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise _invalid("OCR image is malformed, truncated or exceeds decoding limits") from exc


def _storage_error(exc):
    code = "insufficient_storage" if exc.errno == errno.ENOSPC else "backend_failure"
    return BridgeError(code, "NAS OCR private temporary storage is unavailable")


def _executable():
    value = os.environ.get("TAPMESH_TESSERACT", "/usr/bin/tesseract")
    if not value or "\x00" in value or not Path(value).is_absolute():
        raise BridgeError("prerequisite_required", "Configure an absolute Tesseract executable path on the NAS")
    return value


async def _read_bounded(stream, limit):
    output = bytearray()
    while chunk := await stream.read(16 * 1024):
        if len(output) + len(chunk) > limit:
            raise _backend("OCR backend output exceeded its configured limit")
        output.extend(chunk)
    return bytes(output)


async def _settle(task):
    """Finish owned cleanup even if another cancellation arrives while waiting."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    return task.result(), interrupted


async def _terminate_and_drain(process, readers):
    # The session is exclusively ours. Signal its group even if the leader has
    # exited: descendants can still hold output pipes or continue CPU work.
    try:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            raise _backend("Unable to terminate the owned NAS OCR process group") from exc
        try:
            await asyncio.wait_for(process.wait(), _TERMINATE_GRACE_SECONDS)
        except TimeoutError:
            pass
        for attempt in range(3):
            try:
                os.killpg(process.pid, signal.SIGKILL)
                break
            except ProcessLookupError:
                break
            except PermissionError as exc:
                # Permit a brief group-exit race; persistent refusal is an error.
                if attempt == 2:
                    raise _backend("Unable to terminate the owned NAS OCR process group") from exc
                await asyncio.sleep(0.01)
        await process.wait()
    finally:
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def _invoke_tesseract(path, languages):
    process = None
    readers = []
    # No inherited tokens, caller environment, shell, PATH lookup, or extra flags.
    launch = asyncio.create_task(asyncio.create_subprocess_exec(
        _executable(), str(path), "stdout", "-l", languages, "--psm", "11", "tsv",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "OMP_THREAD_LIMIT": "1"},
        cwd=str(path.parent),
    ))
    try:
        try:
            process = await asyncio.shield(launch)
        except asyncio.CancelledError:
            try:
                process, _ = await _settle(launch)
            except OSError:
                pass
            raise
        except OSError as exc:
            raise BridgeError("prerequisite_required", "The NAS Tesseract executable is unavailable") from exc
        readers = [
            asyncio.create_task(_read_bounded(process.stdout, _MAX_STDOUT_BYTES)),
            asyncio.create_task(_read_bounded(process.stderr, _MAX_STDERR_BYTES)),
        ]
        stdout, stderr = await asyncio.gather(*readers)
        await process.wait()
        if process.returncode:
            if any(marker in stderr for marker in (b"Error opening data file", b"Failed loading language", b"couldn't load any languages")):
                raise BridgeError("prerequisite_required", "Install the requested Tesseract language models on the NAS")
            raise BridgeError("backend_failure", "NAS OCR did not complete", details={"exit_code": process.returncode})
        return stdout
    finally:
        if process is not None:
            _, interrupted = await _settle(asyncio.create_task(_terminate_and_drain(process, readers)))
            if interrupted:
                raise asyncio.CancelledError


def _parse_tsv(data, image_size, logical_size):
    if len(data) > _MAX_STDOUT_BYTES:
        raise _backend("OCR output exceeded its configured limit")
    try:
        rows = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise _backend("OCR returned invalid UTF-8 text") from exc
    if not rows or rows[0] != _HEADER or len(rows) > _MAX_ROWS + 1:
        raise _backend("OCR returned an invalid or oversized TSV document")
    image_width, image_height = image_size
    lines = {}
    words = text_characters = 0
    for row in rows[1:]:
        if not row:
            continue
        cells = row.split("\t", 11)
        if len(cells) != 12:
            raise _backend("OCR returned a malformed TSV row")
        try:
            if any(len(value) > 16 for value in cells[:11]):
                raise ValueError
            level, page, block, paragraph, line, word, x, y, width, height = map(int, cells[:10])
            confidence = float(cells[10])
            if not 1 <= level <= 5 or page < 1 or min(block, paragraph, line, word, x, y, width, height) < 0:
                raise ValueError
            if not math.isfinite(confidence) or not -1 <= confidence <= 100:
                raise ValueError
            if x + width > image_width or y + height > image_height:
                raise ValueError
        except ValueError as exc:
            raise _backend("OCR returned invalid row geometry or confidence") from exc
        if level != 5:
            continue
        label = " ".join(cells[11].split())
        if not label:
            continue
        if not all((block, paragraph, line, word, width, height)) or confidence < 0:
            raise _backend("OCR returned an invalid word box")
        if any(ord(character) < 32 or 0x7f <= ord(character) <= 0x9f for character in label):
            raise _backend("OCR returned unsupported control characters")
        words += 1
        text_characters += len(label)
        if words > _MAX_WORDS or text_characters > _MAX_TEXT_CHARACTERS:
            raise _backend("OCR returned too much text")
        key = (page, block, paragraph, line)
        if key not in lines:
            if len(lines) >= _MAX_LINES:
                raise _backend("OCR returned too many text lines")
            lines[key] = {"labels": [], "left": x, "top": y, "right": x + width, "bottom": y + height, "confidence_sum": 0.0}
        item = lines[key]
        item["labels"].append(label)
        item["left"] = min(item["left"], x)
        item["top"] = min(item["top"], y)
        item["right"] = max(item["right"], x + width)
        item["bottom"] = max(item["bottom"], y + height)
        item["confidence_sum"] += confidence
    scale_x = logical_size["width"] / image_width
    scale_y = logical_size["height"] / image_height
    elements = []
    for item in lines.values():
        label = " ".join(item["labels"])
        if len(label) > _MAX_LABEL_CHARACTERS:
            raise _backend("OCR returned an oversized text line")
        elements.append({
            "label": label,
            "value": None,
            "role": "text",
            "bounds": {"x": item["left"] * scale_x, "y": item["top"] * scale_y,
                       "width": (item["right"] - item["left"]) * scale_x,
                       "height": (item["bottom"] - item["top"]) * scale_y},
            "source": "ocr",
            "confidence": item["confidence_sum"] / (100 * len(item["labels"])),
        })
    return elements


async def recognize(image_data: bytes, *, logical_size: dict, languages="eng+ara") -> dict:
    """Recognize a PNG/JPEG screenshot on the NAS and return logical text boxes.

    Raises BridgeError with not_sent side effects on invalid arguments, missing
    prerequisites, backend failures or queue/process deadlines. Cancellation
    propagates after the owned process group and private files are cleaned up.
    One ASGI event loop owns this module's concurrency gate.
    """
    logical = _validate_request(image_data, logical_size, languages)
    global _admitted
    if _admitted >= _MAX_ADMITTED:
        raise BridgeError("deadline_exceeded", "NAS OCR queue is full", retryable=True)
    _admitted += 1
    acquired = False
    try:
        try:
            await asyncio.wait_for(_slots.acquire(), _QUEUE_TIMEOUT_SECONDS)
        except TimeoutError as exc:
            raise BridgeError("deadline_exceeded", "NAS OCR queue deadline exceeded", retryable=True) from exc
        acquired = True
        try:
            with tempfile.TemporaryDirectory(prefix="tapmesh-ocr-") as directory:
                path = Path(directory) / "input.png"
                image_size = _write_verified_image(image_data, logical, path)
                if os.environ.get('TAPMESH_OCR_ENGINE') == 'vision':
                    from .vision_ocr import recognize_file
                    elements, used_languages = await recognize_file(path, logical, languages)
                else:
                    try:
                        tsv = await asyncio.wait_for(_invoke_tesseract(path, languages), _PROCESS_TIMEOUT_SECONDS)
                    except TimeoutError as exc:
                        raise BridgeError("deadline_exceeded", "NAS OCR processing deadline exceeded", retryable=True) from exc
                    elements = _parse_tsv(tsv, image_size, logical)
                    used_languages = languages.split('+')
        except OSError as exc:
            raise _storage_error(exc) from exc
        return {"source": "ocr", "logical_size": logical,
                "engine": "vision" if os.environ.get('TAPMESH_OCR_ENGINE') == 'vision' else 'tesseract', "languages": used_languages,
                "image_size": {"width": image_size[0], "height": image_size[1]}, "elements": elements}
    finally:
        if acquired:
            _slots.release()
        _admitted -= 1
