"""Local system Vision adapter; shares the verified-image and admission bounds."""
import json
import math
import os
from pathlib import Path
import time

from agent.process import Runner
from agent.safety import BridgeError

_capability_cache = None


def helper():
    value = os.environ.get('TAPMESH_NATIVE_TOOLS', '')
    if not value or not Path(value).is_absolute():
        raise BridgeError('prerequisite_required', 'The bundled TapMeshTools helper is unavailable')
    return value


async def capabilities():
    global _capability_cache
    executable = helper()
    if _capability_cache and _capability_cache[0] == executable and time.monotonic() - _capability_cache[1] < 60:
        return dict(_capability_cache[2])
    runner = Runner(output_limit=65536)
    try:
        result = await runner.run([executable, 'ocr-capabilities'], timeout=5)
        value = json.loads(result.stdout)
        languages = value.get('languages')
        if value.get('engine') != 'vision' or not isinstance(languages, list) or not set(languages) <= {'eng', 'ara'}:
            raise ValueError()
        public = {'status': 'available' if languages else 'unavailable', 'engine': 'vision', 'languages': languages}
        _capability_cache = (executable, time.monotonic(), public)
        return dict(public)
    except (ValueError, TypeError, AttributeError) as exc:
        raise BridgeError('backend_failure', 'The bundled OCR helper returned invalid capabilities') from exc
    finally:
        await runner.close()


async def recognize_file(path, logical, requested):
    available = await capabilities()
    languages = [language for language in requested.split('+') if language in available['languages']]
    if not languages:
        raise BridgeError('prerequisite_required', 'The system OCR engine does not support the requested languages')
    runner = Runner(output_limit=1024 * 1024)
    try:
        result = await runner.run([helper(), 'ocr', str(path), '--logical-width', str(logical['width']),
                                   '--logical-height', str(logical['height']), '--languages', '+'.join(languages)], timeout=45)
        value = json.loads(result.stdout)
        if value.get('engine') != 'vision' or value.get('languages') != languages:
            raise ValueError()
        rows = value['elements']
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError()
        elements = []
        total = 0
        for row in rows:
            text, confidence, bounds = row['text'], row['confidence'], row['bounds']
            if not isinstance(text, str) or len(text) > 4096:
                raise ValueError()
            total += len(text)
            if total > 128000 or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                raise ValueError()
            if not isinstance(bounds, dict) or set(bounds) != {'x', 'y', 'width', 'height'}:
                raise ValueError()
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bounds.values()):
                raise ValueError()
            if bounds['x'] < 0 or bounds['y'] < 0 or bounds['width'] <= 0 or bounds['height'] <= 0 or bounds['x'] + bounds['width'] > logical['width'] + .01 or bounds['y'] + bounds['height'] > logical['height'] + .01:
                raise ValueError()
            elements.append({'label': text, 'value': None, 'confidence': confidence, 'bounds': bounds, 'role': 'text', 'source': 'ocr'})
        return elements, languages
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise BridgeError('backend_failure', 'The bundled OCR helper returned invalid text boxes') from exc
    finally:
        await runner.close()
