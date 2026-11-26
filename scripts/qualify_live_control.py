#!/usr/bin/env python3
"""Measure a wired TapMesh session and guide an owner-attended input trial."""

import argparse
import asyncio
import base64
from contextlib import aclosing, asynccontextmanager, suppress
from datetime import date
import io
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import median
import struct
import sys
from time import monotonic
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image, ImageChops, ImageStat


SESSION = 'qualification'
DEFAULT_PROFILE = Path.home() / 'Library/Application Support/TapMesh/Local'
REPORT_DIRECTORY = Path(__file__).resolve().parents[1] / 'docs'
LIVE_HEADER = struct.Struct('!BI')
LIVE_FORMAT, LIVE_PARAMS, LIVE_FRAME, LIVE_END = 1, 2, 3, 5
LIVE_FRAME_HEADER = struct.Struct('!BQ')
SAMPLE_SECONDS = 10
SWIPE_COUNT = 5
SWIPE_WINDOW_SECONDS = 3
SWIPE_STILL_SECONDS = .35
SWIPE_CHANGE_THRESHOLD = 7
TYPING_SAMPLE = 'TapMesh trial. ' * 13 + 'Done.'
READ_ONLY_CHECKS = ('Live video performance', 'Viewer closes on disconnect', 'Screenshot latency')
ATTENDED_CHECKS = (
    'Home Screen swipe latency', 'Landscape video and tap', 'Lock interrupts typing',
    'USB unplug and reconnect', 'Wi-Fi picture fallback', 'Two wired devices', 'Sleep and wake',
    'AI consent and Stop/Resume',
)


class QualificationError(RuntimeError):
    """Fixed, report-safe diagnostic with no device or connection details."""


def connect(profile):
    config = json.loads((profile / 'connection.json').read_text())
    if not isinstance(config, dict) or not isinstance(config.get('api_url'), str):
        raise QualificationError('The TapMesh connection file has no valid service address.')
    address = urlsplit(config['api_url'])
    if (address.scheme != 'http' or address.hostname not in {'127.0.0.1', '::1', 'localhost'}
            or address.username or address.password or address.path not in {'', '/'}
            or address.query or address.fragment or not address.port):
        raise QualificationError('Connection must use a local TapMesh service.')
    token_file = profile / 'server.token'
    if token_file.is_symlink():
        raise QualificationError('The TapMesh service token must be a local profile file.')
    token = token_file.read_text().strip()
    if not token:
        raise QualificationError('The TapMesh service token is missing.')
    return httpx.AsyncClient(base_url=address.geturl(), headers={'Authorization': f'Bearer {token}'},
                             timeout=12, trust_env=False, follow_redirects=False)


async def call(client, tool, arguments):
    timeout = 40 if tool == 'type_text' else 12
    response = await client.post('/api/call', json={'session_id': SESSION, 'tool': tool, 'arguments': arguments}, timeout=timeout)
    response.raise_for_status()
    reply = response.json()
    if not isinstance(reply, dict):
        raise QualificationError('The service returned an invalid tool response.')
    return reply


async def screenshot(client, device):
    reply = await call(client, 'take_screenshot', {'device_id': device, 'format': 'jpeg', 'max_width': 640, 'require_fresh': True})
    if reply.get('status') != 'success':
        raise QualificationError('A fresh screenshot could not be captured.')
    capture = reply.get('data')
    image = capture.get('image') if isinstance(capture, dict) else None
    if not isinstance(image, dict) or not isinstance(image.get('data'), str) or not image['data']:
        raise QualificationError('The service returned an incomplete screenshot.')
    try:
        with Image.open(io.BytesIO(base64.b64decode(image['data'], validate=True))) as frame:
            frame.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise QualificationError('The service returned an invalid screenshot image.') from exc
    return capture


async def live_viewers(client):
    response = await client.get('/health', timeout=2)
    response.raise_for_status()
    health = response.json()
    count = health.get('live_viewers') if isinstance(health, dict) else None
    return count if type(count) is int and count >= 0 else None


async def probe_viewers(client):
    with suppress(httpx.HTTPError, ValueError):
        return await live_viewers(client)


async def live_packets(response):
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while len(buffer) >= LIVE_HEADER.size:
            kind, length = LIVE_HEADER.unpack_from(buffer)
            if length > 8 * 1024 * 1024:
                raise QualificationError('The live stream returned an oversized packet.')
            end = LIVE_HEADER.size + length
            if len(buffer) < end:
                break
            payload = bytes(buffer[LIVE_HEADER.size:end])
            del buffer[:end]
            if kind == LIVE_FRAME and length < LIVE_FRAME_HEADER.size + 5:
                raise QualificationError('TapMesh returned an incomplete live frame.')
            yield kind, payload
    if buffer:
        raise QualificationError('The live stream closed with an incomplete packet.')


@asynccontextmanager
async def live_messages(client, device):
    path = '/api/live/' + quote(device, safe=':')
    async with client.stream('GET', path, params={'session_id': SESSION}) as response:
        response.raise_for_status()
        if not response.headers.get('content-type', '').startswith('application/vnd.tapmesh.live'):
            raise QualificationError('TapMesh did not return a live video stream.')
        async with aclosing(live_packets(response)) as messages:
            yield messages


async def measure_live(client, device):
    before = await live_viewers(client)
    if before is None:
        raise QualificationError('The service did not report the live viewer count.')
    if before != 0:
        raise QualificationError('Close other live previews before measuring viewer cleanup.')
    started = monotonic()
    first = None
    during = None
    frames = 0
    complete = False
    reason = None
    try:
        async with live_messages(client, device) as packets:
            probe = asyncio.create_task(probe_viewers(client))
            try:
                async with asyncio.timeout(SAMPLE_SECONDS) as deadline:
                    async for kind, _ in packets:
                        if kind == LIVE_END:
                            raise QualificationError('Live video ended before the sample completed.')
                        if kind == LIVE_FRAME:
                            if first is None:
                                first = monotonic() - started
                                deadline.reschedule(asyncio.get_running_loop().time() + SAMPLE_SECONDS)
                            else:
                                frames += 1
                    raise QualificationError('Live video closed before the sample completed.')
            finally:
                during = await probe
    except TimeoutError:
        complete = first is not None
        if not complete:
            reason = 'Live video did not produce a frame.'
    except QualificationError as exc:
        reason = str(exc)
    except (httpx.HTTPError, ValueError):
        reason = 'The live video connection could not complete the sample.'
    after = await probe_viewers(client)
    for _ in range(20):
        if after in (0, None):
            break
        await asyncio.sleep(.1)
        after = await probe_viewers(client)
    result = {'first_frame_ms': round(first * 1000, 1) if first is not None else None,
              'frame_count': frames, 'frames_per_second': frames / SAMPLE_SECONDS if complete else None,
              'viewer_count_before': before, 'viewer_count_during': during, 'viewer_count_after': after}
    if reason:
        result['reason'] = reason
    return result


def score(samples):
    ordered = sorted(samples)
    return {'median_ms': round(median(ordered), 1), 'p90_ms': round(ordered[ceil(.9 * len(ordered)) - 1], 1),
            'max_ms': round(ordered[-1], 1), 'samples': len(ordered)}


def avcc_units(payload):
    offset = 0
    result = []
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise QualificationError('The live video contains an incomplete HEVC unit.')
        length = int.from_bytes(payload[offset:offset + 4], 'big')
        offset += 4
        if length < 2 or offset + length > len(payload):
            raise QualificationError('The live video contains an invalid HEVC unit.')
        result.append(payload[offset:offset + length])
        offset += length
    return result


def live_crop(payload):
    try:
        value = json.loads(payload)
        if value['version'] != 1 or value['codec'] != 'hevc':
            raise ValueError
        width, height = value['coded_width'], value['coded_height']
        crop = value.get('crop') or {'x': 0, 'y': 0, 'width': width, 'height': height}
        x, y, w, h = (crop[name] for name in ('x', 'y', 'width', 'height'))
        if not all(type(n) is int for n in (width, height, x, y, w, h)):
            raise ValueError
        if not (0 < width <= 16384 and 0 < height <= 16384 and x >= 0 and y >= 0
                and w > 0 and h > 0 and x + w <= width and y + h <= height):
            raise ValueError
        return x, y, x + w, y + h
    except (KeyError, TypeError, ValueError) as exc:
        raise QualificationError('The live video did not supply a usable display crop.') from exc


def video_decoder_factory():
    if not __package__:
        sys.path.insert(0, str(REPORT_DIRECTORY.parent))
    from agent.native_physical import VTFrameDecoder
    return VTFrameDecoder


def differs(first, second):
    return ImageStat.Stat(ImageChops.difference(first, second)).mean[0] >= SWIPE_CHANGE_THRESHOLD


class SwipeFrames:
    def __init__(self):
        self.latest = None
        self.still_reference = None
        self.still_since = 0
        self.portrait = False
        self.pending = None

    def observe(self, frame, at, *, portrait):
        self.latest, self.portrait = frame, portrait
        if self.still_reference is None or differs(self.still_reference, frame):
            self.still_reference, self.still_since = frame, at
        if self.pending:
            reference, started, change = self.pending
            if at >= started and portrait and not change.done() and differs(reference, frame):
                change.set_result(at)

    async def warm_baseline(self, reader):
        async with asyncio.timeout(SWIPE_WINDOW_SECONDS):
            while True:
                if reader.done():
                    reader.result()
                if self.latest is not None and monotonic() - self.still_since >= SWIPE_STILL_SECONDS:
                    if not self.portrait:
                        raise QualificationError('The swipe trial requires a portrait live view.')
                    return self.latest
                await asyncio.sleep(.02)


def drain_video(decoder, crop, frames):
    decoded = decoder.drain()
    try:
        for status, handle in decoded:
            if status != 0 or handle is None:
                if frames.pending and not frames.pending[2].done():
                    raise QualificationError('The live decoder dropped a frame during swipe measurement.')
                continue
            at = monotonic()
            image = decoder.image(handle)
            if image is None:
                continue
            left, top, right, bottom = crop
            if right > image.width or bottom > image.height:
                raise QualificationError('The decoded live frame did not match its display crop.')
            # Ignore status and navigation strips, then compare only reduced luma in memory.
            inset = (bottom - top) // 8
            reduced = image.resize((96, 96), Image.Resampling.BILINEAR,
                                   box=(left, top + inset, right, bottom - inset)).convert('L')
            frames.observe(reduced, at, portrait=right - left < bottom - top)
    finally:
        for _, handle in decoded:
            if handle is not None:
                decoder.release(handle)


def close_video_decoder(decoder):
    decoder.close()
    for _, handle in decoder.drain():
        if handle is not None:
            decoder.release(handle)


async def read_swipe_frames(packets, frames, decoder_factory):
    decoder = None
    crop = None
    params = None
    next_packet = asyncio.create_task(anext(packets))
    try:
        while True:
            if decoder is not None:
                drain_video(decoder, crop, frames)
            if not next_packet.done():
                await asyncio.wait((next_packet,), timeout=.004 if decoder is not None else None)
                continue
            kind, payload = next_packet.result()
            if kind == LIVE_END:
                raise QualificationError('Live video ended during the swipe trial.')
            if kind == LIVE_FORMAT:
                updated_crop = live_crop(payload)
                if updated_crop != crop and decoder is not None:
                    close_video_decoder(decoder)
                    decoder = None
                crop = updated_crop
            elif kind == LIVE_PARAMS:
                updated = avcc_units(payload)
                if tuple((nal[0] >> 1) & 0x3f for nal in updated) != (32, 33, 34):
                    raise QualificationError('The live video supplied invalid HEVC parameters.')
                if updated != params and decoder is not None:
                    close_video_decoder(decoder)
                    decoder = None
                params = updated
            elif kind == LIVE_FRAME and crop and params:
                keyframe, _ = LIVE_FRAME_HEADER.unpack_from(payload)
                if decoder is None and keyframe == 1:
                    decoder = decoder_factory(*params)
                if decoder is not None:
                    decoder.feed(avcc_units(payload[LIVE_FRAME_HEADER.size:]))
            next_packet = asyncio.create_task(anext(packets))
    except StopAsyncIteration as exc:
        raise QualificationError('Live video closed during the swipe trial.') from exc
    finally:
        next_packet.cancel()
        await asyncio.gather(next_packet, return_exceptions=True)
        if decoder is not None:
            close_video_decoder(decoder)


REPORTABLE_INPUT_ERRORS = {
    'approval_required', 'approval_denied', 'approval_expired', 'device_held', 'device_locked',
    'protected_action_requires_human', 'stale_observation', 'connection_changed', 'device_disconnected',
    'unsupported_capability', 'prerequisite_required', 'delivery_outcome_unknown', 'cancelled',
}


def elapsed_ms(start, end=None):
    return round(((monotonic() if end is None else end) - start) * 1000, 1)


async def swipe_attempt(client, device, index, frames, reader, result):
    capture_started = monotonic()
    capture = await screenshot(client, device)
    result['screenshot_anchor_ms'] = elapsed_ms(capture_started)
    size = reported_size(capture.get('logical_size'))
    if (capture.get('orientation') != 'portrait' or capture.get('input_geometry_qualified') is False
            or not size or size['width'] >= size['height'] or not isinstance(capture.get('observation_id'), str)
            or not capture['observation_id']):
        raise QualificationError('The swipe trial requires a qualified portrait capture.')
    width, height = size['width'], size['height']
    start, end = (.78, .22) if index % 2 == 0 else (.22, .78)
    coordinates = {'y': height * .55, 'coordinate_space': 'device_points',
                   'observation_id': capture['observation_id']}
    arguments = {'device_id': device, 'start': {**coordinates, 'x': width * start},
                 'end': {**coordinates, 'x': width * end}, 'duration_ms': 100}
    baseline = await frames.warm_baseline(reader)
    change = asyncio.get_running_loop().create_future()
    started = monotonic()
    frames.pending = baseline, started, change
    try:
        async with asyncio.timeout(SWIPE_WINDOW_SECONDS):
            reply = await call(client, 'swipe', arguments)
            result['request_ms'] = elapsed_ms(started)
            if reply.get('status') != 'success':
                error = reply.get('error')
                code = error.get('code') if isinstance(error, dict) else None
                delivery = error.get('side_effect_state') if isinstance(error, dict) else None
                result['request_status'] = 'refused'
                if isinstance(code, str) and code in REPORTABLE_INPUT_ERRORS:
                    result['error_code'] = code
                if delivery in ('not_sent', 'unknown', 'confirmed'):
                    result['side_effect_state'] = delivery
                raise QualificationError('The service refused the swipe. No retry was sent.')
            result['request_status'] = 'success'
            result['changed_live_frame_ms'] = elapsed_ms(started, await change)
    finally:
        result['attempt_elapsed_ms'] = elapsed_ms(started)
        frames.pending = None
        change.cancel()


async def swipe_latency(client, device, *, decoder_factory=None):
    attempts = []
    result = {'measurement': 'Swipe request start to the first changed decoded frame on a warm live stream.',
              'attempts': attempts}
    try:
        decoder_factory = decoder_factory or video_decoder_factory()
        async with live_messages(client, device) as packets:
            frames = SwipeFrames()
            reader = asyncio.create_task(read_swipe_frames(packets, frames, decoder_factory))
            try:
                await frames.warm_baseline(reader)
                for index in range(SWIPE_COUNT):
                    attempt = {'attempt': index + 1}
                    attempts.append(attempt)
                    await swipe_attempt(client, device, index, frames, reader, attempt)
            except TimeoutError as exc:
                if reader.done():
                    reader.result()
                raise QualificationError('The trial timed out waiting for a stable or changed live frame.') from exc
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
    except QualificationError as exc:
        result['reason'] = str(exc)
    except (ImportError, RuntimeError, httpx.HTTPError, OSError, ValueError):
        result['reason'] = 'TapMesh could not decode or measure the local live video with VideoToolbox.'
    samples = [attempt['changed_live_frame_ms'] for attempt in attempts if 'changed_live_frame_ms' in attempt]
    return {**result, **(score(samples) if samples else {'samples': 0})}


def observed(step, instruction):
    input(f'\n{instruction}\nPress Return when ready. ')
    while True:
        answer = input('Observed the expected result? [pass/fail/skip] ').strip().lower()
        if answer in {'pass', 'fail', 'skip'}:
            return {'check': step, 'status': {'pass': 'passed', 'fail': 'failed', 'skip': 'skipped'}[answer]}


def reported_size(value):
    if not isinstance(value, dict):
        return None
    width, height = value.get('width'), value.get('height')
    if not all(type(n) in (int, float) and 0 < n <= 16384 and isfinite(n) for n in (width, height)):
        return None
    return {'width': width, 'height': height}


def reported_orientation(value):
    labels = {'portrait': 'portrait', 'portraitUpsideDown': 'portrait_upside_down',
              'portrait_upside_down': 'portrait_upside_down', 'landscapeLeft': 'landscape_left',
              'landscape_left': 'landscape_left', 'landscapeRight': 'landscape_right',
              'landscape_right': 'landscape_right'}
    return labels.get(value, 'unknown') if isinstance(value, str) else 'unknown'


async def landscape_trial(client, device):
    input('\nOpen a suitable app, rotate the phone to landscape-left, and let TapMesh settle. '
          'Press Return to record the display geometry. ')
    evidence = {}
    with suppress(QualificationError, httpx.HTTPError, ValueError, TypeError):
        capture = await screenshot(client, device)
        evidence['capture'] = {
            'orientation': reported_orientation(capture.get('orientation')),
            'logical_size': reported_size(capture.get('logical_size')),
            'image_size': reported_size(capture.get('image_size')),
            'input_geometry_qualified': capture.get('input_geometry_qualified') is not False,
        }
        reply = await call(client, 'get_device_info', {'device_id': device})
        data = reply.get('data')
        display = data.get('screen') if isinstance(data, dict) else None
        if reply.get('status') == 'success' and isinstance(display, dict):
            orientation = reported_orientation(display.get('orientation'))
            logical = reported_size(display.get('logical_size'))
            evidence['current_display'] = {'orientation': orientation, 'logical_size': logical}
            if logical and orientation != 'unknown':
                evidence['matches_current_display'] = (
                    evidence['capture']['logical_size'] == logical
                    and evidence['capture']['orientation'] == orientation
                )
    result = observed('Landscape video and tap', 'Verify the full landscape preview. Refresh the screen in TapMesh, '
                      'then tap one harmless target from the app and check where it landed.')
    if evidence:
        result['read_only_evidence_before_manual_tap'] = evidence
    if result['status'] == 'failed':
        code = input('Enter the error code shown in TapMesh, or press Return to leave it blank. ').strip()
        if code in REPORTABLE_INPUT_ERRORS:
            result['tap_error_code'] = code
    return result


async def attended(client, device, checks):
    if not sys.stdin.isatty():
        raise QualificationError('Attended qualification requires an interactive terminal and the device owner.')
    input('Allow this phone in TapMesh, then open the portrait Home Screen on a page with another page to its right. '
          'Press Return to warm the live video and send five alternating swipes. ')
    result = await swipe_latency(client, device)
    passed = result['samples'] == SWIPE_COUNT and result['median_ms'] <= 250
    checks.append({'check': 'Home Screen swipe latency', **result, 'status': 'passed' if passed else 'failed'})

    checks.append(await landscape_trial(client, device))
    input('\nOpen an empty scratch note. Confirm the US keyboard layout and focus the note. '
          'Press Return to send 200 characters. Lock the phone after the first few characters appear. ')
    error = {}
    request_status = 'unconfirmed'
    started = monotonic()
    with suppress(QualificationError, httpx.HTTPError, ValueError):
        reply = await call(client, 'type_text', {'device_id': device, 'text': TYPING_SAMPLE, 'keyboard_layout': 'us'})
        if reply.get('status') in ('success', 'error'):
            request_status = reply['status']
        if request_status == 'error' and isinstance(reply.get('error'), dict):
            error = reply['error']
    request_elapsed = elapsed_ms(started)
    message = error.get('message')
    lock_refused = (error.get('code') == 'device_locked' or
                    (error.get('code') == 'protected_action_requires_human' and isinstance(message, str)
                     and message.startswith(('The device is locked.', 'The device locked during input.'))))
    started_typing = lock_refused and error.get('side_effect_state') == 'unknown'
    typing = observed('Lock interrupts typing', 'Unlock the phone yourself and verify typing stopped partway through '
                      'and no text was entered on the lock screen.')
    if not started_typing and typing['status'] == 'passed':
        typing['status'] = 'failed'
    typing['lock_error_received'] = lock_refused
    typing['input_had_started'] = started_typing
    typing['request_status'] = request_status
    typing['request_elapsed_ms'] = request_elapsed
    checks.append(typing)

    instructions = (
        ('USB unplug and reconnect', 'Unplug the cable during live video and reconnect it. Verify pictures fall back and live video resumes.'),
        ('Wi-Fi picture fallback', 'Select a Wi-Fi-only phone. Verify pictures and the cable hint appear without errors.'),
        ('Two wired devices', 'Connect a second wired device. Select each one in turn and verify it gets the live stream. TapMesh uses one live tunnel at a time.'),
        ('Sleep and wake', 'Put this Mac to sleep for one minute, then wake it and verify live video resumes.'),
        ('AI consent and Stop/Resume', 'Remove the trial phone from the allowed list. In a separate MCP client, select it and try one harmless tap. '
         'Verify the Allow card and blocked input, then Allow and verify the retry works. Stop and Resume the client from TapMesh. '
         'Quit while held, reopen, and verify permission was kept and the hold cleared.'),
    )
    checks.extend(observed(step, instruction) for step, instruction in instructions)


def write_report(directory, report):
    directory.mkdir(parents=True, exist_ok=True)
    base = directory / f'LIVE_CONTROL_QUALIFICATION_{date.today().isoformat()}'
    suffix = 2
    while base.with_suffix('.json').exists() or base.with_suffix('.md').exists():
        base = directory / f'LIVE_CONTROL_QUALIFICATION_{date.today().isoformat()}_{suffix}'
        suffix += 1
    base.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# TapMesh live-control qualification', '', f"Mode: {report['mode']}", f"Result: {report['status']}", '',
             '| Check | Result | Measurements |', '| --- | --- | --- |']
    for row in report['checks']:
        metrics = ', '.join(f"{key.replace('_', ' ')}: {value}" for key, value in row.items()
                            if key not in {'check', 'status'})
        lines.append(f"| {row['check']} | {row['status']} | {metrics} |")
    base.with_suffix('.md').write_text('\n'.join(lines) + '\n')
    return base


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog='For frame-rate measurements, play moving content on the phone and close other live previews.')
    parser.add_argument('--device', required=True, help='Explicit wired physical device ID')
    parser.add_argument('--attended', action='store_true', help='Enable input only with the owner present')
    parser.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--report-directory', type=Path, default=REPORT_DIRECTORY)
    args = parser.parse_args(argv)
    if not args.device.startswith('ios:') or not 4 < len(args.device) <= 256:
        parser.error('Choose an explicit physical device.')
    checks = []
    report = {'mode': 'owner attended' if args.attended else 'read-only', 'checks': checks}
    try:
        async with connect(args.profile) as client:
            selected = await call(client, 'select_device', {'device_id': args.device})
            details = selected.get('data')
            if (selected.get('status') != 'success' or not isinstance(details, dict)
                    or details.get('selected_device_id') != args.device or details.get('ready') is not True):
                raise QualificationError('The selected phone is unavailable.')
            try:
                live = await measure_live(client, args.device)
            except (QualificationError, httpx.HTTPError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, QualificationError) else 'The live video test could not start.'
                checks.append({'check': READ_ONLY_CHECKS[0], 'status': 'failed', 'reason': reason})
            else:
                first, fps = live['first_frame_ms'], live['frames_per_second']
                passed = first is not None and first <= 2000 and fps is not None and fps >= 50
                checks.append({'check': READ_ONLY_CHECKS[0], 'status': 'passed' if passed else 'failed', **live})
                cleaned = (live['viewer_count_before'], live['viewer_count_during'], live['viewer_count_after']) == (0, 1, 0)
                checks.append({'check': READ_ONLY_CHECKS[1], 'status': 'passed' if cleaned else 'failed'})
            captures = []
            for _ in range(20):
                started = monotonic()
                try:
                    await screenshot(client, args.device)
                except (QualificationError, httpx.HTTPError, ValueError):
                    break
                captures.append((monotonic() - started) * 1000)
            metrics = score(captures) if captures else {'samples': 0}
            passed = len(captures) == 20 and metrics['median_ms'] <= 250
            checks.append({'check': READ_ONLY_CHECKS[2], 'status': 'passed' if passed else 'failed', **metrics})
            if args.attended:
                await attended(client, args.device, checks)
    except QualificationError as exc:
        checks.append({'check': str(exc), 'status': 'failed'})
    except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
        checks.append({'check': 'Qualification was interrupted.', 'status': 'failed'})
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        checks.append({'check': 'The local service or its response was unavailable.', 'status': 'failed'})
    completed = {row['check'] for row in checks}
    expected = READ_ONLY_CHECKS + (ATTENDED_CHECKS if args.attended else ())
    checks.extend({'check': step, 'status': 'skipped'} for step in expected if step not in completed)
    report['status'] = 'passed' if checks and all(row['status'] == 'passed' for row in checks) else 'failed'
    print(f"Qualification {report['status']}. Report: {write_report(args.report_directory, report)}")
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
