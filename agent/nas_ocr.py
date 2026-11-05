"""Private NAS OCR port; screenshots remain bound to the Mac observation."""
import asyncio
import math
import os
import time
from urllib.parse import urlsplit
import httpx
from .safety import BridgeError, read_secret


class NASOCR:
    def __init__(self, url, token, *, client=None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'}):
            raise RuntimeError('OCR processor must use the private loopback SSH port')
        self.client = client or httpx.AsyncClient(base_url=url, headers={'Authorization': 'Bearer ' + token},
                                                  timeout=50 if os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1' else 15, follow_redirects=False, trust_env=False,
                                                  limits=httpx.Limits(max_connections=3, max_keepalive_connections=1))
        self.owns_client = client is None
        self.health_at = 0.0
        self.health_value = False
        self.name = 'Local OCR' if os.environ.get('TAPMESH_BUNDLED_RUNTIME')=='1' else 'NAS OCR'

    @classmethod
    def configured(cls):
        url = os.environ.get('TAPMESH_OCR_URL')
        return cls(url, read_secret(os.environ.get('TAPMESH_AGENT_TOKEN_FILE'))) if url else None

    async def available(self):
        now = time.monotonic()
        if now - self.health_at > 5:
            self.health_at = now
            try:
                result = await self.client.get('/internal/ocr/health', timeout=2)
                self.health_value = result.status_code == 200 and result.json().get('status') == 'available'
            except (httpx.HTTPError, ValueError):
                self.health_value = False
        return self.health_value

    async def close(self):
        if self.owns_client:
            await self.client.aclose()

    async def inspect(self, backend, device, context):
        if not await self.available():
            raise BridgeError('prerequisite_required', self.name+' processor is unavailable; reconnect and check runtime diagnostics')
        captured_at = time.monotonic()
        capture = await backend.invoke('take_screenshot', device,
                                       {'format': 'png', 'max_width': 2048, 'require_fresh': True}, context)
        try:
            response = await self.client.post('/internal/ocr', json={
                'image_base64': capture['image']['data'], 'logical_size': capture['logical_size']})
            if response.status_code != 200:
                raise BridgeError('backend_failure', self.name+' did not confirm a complete observation')
            result = response.json()
            logical = capture['logical_size']
            elements = result['elements']
            if (result.get('source') != 'ocr' or result.get('logical_size') != logical
                    or not isinstance(elements, list) or len(elements) > 1000):
                raise ValueError('Invalid OCR observation')
            normalized = []
            for item in elements:
                box = item['bounds']
                values = [box[key] for key in ('x', 'y', 'width', 'height')]
                if (any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in values)
                        or min(values) < 0 or box['x'] + box['width'] > logical['width'] + .01
                        or box['y'] + box['height'] > logical['height'] + .01
                        or not isinstance(item['label'], str) or len(item['label'].encode('utf-8')) > 8192):
                    raise ValueError('Invalid OCR target geometry')
                normalized.append({'label': item['label'], 'value': None, 'role': 'text',
                                   'bounds': box, 'source': 'ocr', 'confidence': item.get('confidence')})
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise BridgeError('backend_failure', self.name+' response could not be validated') from exc
        return {'elements': normalized, 'logical_size': logical, 'source': 'ocr',
                'orientation': capture.get('orientation', 'unknown'),
                '_frame_pixel_sha256': capture.get('frame_pixel_sha256'),
                '_captured_monotonic': captured_at, '_observed_at': capture.get('observed_at')}
