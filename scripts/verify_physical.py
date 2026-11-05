#!/usr/bin/env python3
"""Qualify a named real device only inside the installed TapMesh fixture."""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import httpx
from agent.native_physical import frame_unavailable

STATE = Path.home() / "Library/Application Support/TapMesh"
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--name', required=True, help='Report filename label, e.g. ipad or iphone')
    parser.add_argument('--text', action='store_true', help='Explicitly authorize fixture-only Unicode device clipboard paste')
    parser.add_argument('--tap-point', nargs=2, type=float, metavar=('X', 'Y'), help='Operator-observed Increment button center in device points')
    parser.add_argument('--expected-size', nargs=2, type=float, metavar=('WIDTH', 'HEIGHT'), help='Required observed geometry for an operator-provided point')
    parser.add_argument('--pinch-point', nargs=2, type=float, metavar=('X', 'Y'), help='Operator-observed fixture gesture-surface center in device points')
    args = parser.parse_args()
    if args.name not in {'ipad', 'iphone'}:
        parser.error('name must be ipad or iphone')
    if bool(args.tap_point) != bool(args.expected_size):
        parser.error('tap-point and expected-size are required together')
    if args.pinch_point and not args.expected_size:
        parser.error('pinch-point requires expected-size')
    session = 'physical-' + args.name + '-qualification'
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'device_class': args.name, 'checks': []}
    directory = STATE / 'qualification'
    directory.mkdir(mode=0o700, exist_ok=True)
    owner = 'Bearer ' + (STATE / 'approvals.token').read_text().strip()
    with httpx.Client(base_url='http://127.0.0.1:48793', headers={'Authorization': 'Bearer ' + (STATE / 'server.token').read_text().strip()}, timeout=120) as client:
        def call(tool, values=None, authorize=False):
            payload = {'session_id': session, 'tool': tool, 'arguments': {'device_id': args.device_id, **(values or {})}}
            result = client.post('/api/call', json=payload)
            result.raise_for_status()
            value = result.json()
            # A read may activate Apple's lazy transport. Discard that older
            # epoch and request one new observation; mutations are never retried.
            if tool in {'take_screenshot', 'list_elements'} and value.get('error', {}).get('code') == 'stale_observation':
                result = client.post('/api/call', json=payload)
                result.raise_for_status()
                value = result.json()
            if authorize and value.get('error', {}).get('code') == 'approval_required':
                decision = client.post('/api/approvals/' + value['error']['details']['approval_id'], headers={'X-DeviceBridge-Local-Authorization': owner}, json={'decision': 'allow'})
                decision.raise_for_status()
                result = client.post('/api/call', json=payload)
                result.raise_for_status()
                value = result.json()
            if value.get('status') != 'success':
                raise RuntimeError(tool + ': ' + json.dumps(value.get('error', {})))
            return value['data']

        def passed(name, **metadata):
            report['checks'].append({'name': name, 'status': 'passed', **metadata})
            print(name + ': passed', flush=True)

        def inspect():
            data = call('list_elements', {'source': 'ocr', 'max_elements': 200})
            assert all(e['source'] == 'ocr' for e in data['elements'])
            return data

        def capture():
            data = call('take_screenshot', {'format': 'png', 'max_width': 1200, 'require_fresh': True})
            image = base64.b64decode(data['image']['data'])
            assert not frame_unavailable(image), 'Display is black/unavailable; unlock is a human handoff'
            path = directory / (args.name + '.png')
            path.write_bytes(image); path.chmod(0o600)
            assert data['processed_on'] == 'server'
            return data

        try:
            info = call('get_device_info')
            assert info['backend'] == 'physical' and info['ready']
            report.update(model=info['model'], os_version=info['os_version'])
            call('launch_app', {'bundle_id': 'io.tapmesh.fixture'}, authorize=True)
            time.sleep(.5)
            screen = capture()
            passed('fixture launch and real screen through NAS', logical_size=screen['logical_size'], image_size=screen['image_size'])
            before = inspect()
            labels = [e['label'] for e in before['elements']]
            assert any('TapMesh test device' in label for label in labels), 'Owned fixture is not visible; no input sent'
            old_count = int(next(re.search(r'Tap count:\s*(\d+)', label).group(1) for label in labels if re.search(r'Tap count:\s*(\d+)', label)))
            if args.tap_point:
                anchor = capture()
                assert anchor['logical_size'] == dict(zip(('width', 'height'), args.expected_size)), 'Display changed; review coordinates before any input'
                call('tap', {'target': {'x': args.tap_point[0], 'y': args.tap_point[1], 'coordinate_space': 'device_points', 'observation_id': anchor['observation_id']}}, authorize=True)
            else:
                call('tap_text', {'match': {'text': 'Increment', 'match': 'exact', 'case_sensitive': False}, 'source': 'ocr'}, authorize=True)
            after = inspect()
            new_count = int(next(re.search(r'Tap count:\s*(\d+)', e['label']).group(1) for e in after['elements'] if re.search(r'Tap count:\s*(\d+)', e['label'])))
            assert new_count == old_count + 1, 'A delivered tap did not confirm the expected fixture counter change'
            passed('observation-anchored physical tap with fixture readback', before=old_count, after=new_count, orientation=screen.get('orientation'))
            if args.pinch_point:
                def scale(snapshot):
                    return float(next(re.search(r'Scale:\s*([\d.]+)', e['label']).group(1) for e in snapshot['elements'] if re.search(r'Scale:\s*([\d.]+)', e['label'])))
                before_scale = scale(after)
                anchor = capture()
                assert anchor['logical_size'] == dict(zip(('width', 'height'), args.expected_size))
                call('pinch', {'center': {'x': args.pinch_point[0], 'y': args.pinch_point[1], 'coordinate_space': 'device_points', 'observation_id': anchor['observation_id']}, 'scale': 1.3, 'duration_ms': 500}, authorize=True)
                after_scale = scale(inspect())
                assert abs(after_scale - 1.3) < .08 and abs(after_scale - before_scale) > .1, 'Expected fixture scale change was not observed'
                passed('two-contact pinch with fixture scale readback', before=before_scale, after=after_scale, orientation=screen.get('orientation'))
            if args.text:
                call('tap_text', {'match': {'text': 'Enter name', 'match': 'exact'}, 'source': 'ocr'}, authorize=True)
                result = call('type_text', {'text': 'TapMesh تجربة', 'input_method': 'clipboard'}, authorize=True)
                assert result['clipboard_changed'] is True
                text = inspect()
                assert any('TapMesh تجربة' in e['label'] for e in text['elements']), 'Exact Unicode OCR readback was not observed; review the private screenshot'
                passed('explicit fixture Unicode clipboard paste with exact OCR readback', clipboard_changed=True, generic_business_success_claimed=False)
                # Exact visual readback remains a separate evidence step.
            capture()
            report['status'] = 'passed'
        except Exception as error:
            report['status'] = 'failed'
            report['failure'] = str(error)
            raise
        finally:
            path = ROOT / 'docs' / (args.name.upper() + '_QUALIFICATION.json')
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print('Report:', path, flush=True)


if __name__ == '__main__':
    main()
