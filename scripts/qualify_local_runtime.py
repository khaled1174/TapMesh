#!/usr/bin/env python3
"""Exercise relocated bundled services in a new profile, without device input."""
import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import stat

import httpx


def receive(process, seconds=60):
    deadline = time.monotonic() + seconds
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        output = bytearray()
        while time.monotonic() < deadline:
            if selector.select(min(.2, max(0, deadline-time.monotonic()))):
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    raise RuntimeError('Bundled supervisor exited before readiness')
                output.extend(chunk)
                if len(output) > 16384:
                    raise RuntimeError('Unbounded supervisor output')
                if b'\n' in output:
                    value = json.loads(output.split(b'\n', 1)[0])
                    assert value.get('status') == 'ready', value
                    return value
    raise TimeoutError('Bundled supervisor readiness deadline')


def services_stopped(configuration):
    with httpx.Client(timeout=.3, trust_env=False) as client:
        for url in (configuration['agent_url'], configuration['api_url']):
            try:
                client.get(url+'/health')
                return False
            except httpx.HTTPError:
                pass
    return True


async def mcp_check(launcher, profile, cwd, env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # An inherited proxy must never see a private bearer. With this unreachable
    # proxy and an empty bypass list, correct local transport still succeeds.
    poisoned = dict(env, HTTP_PROXY='http://127.0.0.1:9', ALL_PROXY='http://127.0.0.1:9', NO_PROXY='')
    parameters = StdioServerParameters(command=str(launcher), args=['--state-dir', str(profile)], env=poisoned, cwd=str(cwd))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result.server_info.name == 'TapMesh'
            assert len((await session.list_tools()).tools) == 50


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(args.bundle)],check=True)
    report = {'product': 'TapMesh', 'checks': [], 'status': 'running'}

    def checked(name):
        report['checks'].append({'name': name, 'status': 'passed'})
        print(name+': passed', flush=True)

    cache = Path.home()/'Library/Caches/TapMesh'
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='TapMesh relocated ', dir=cache) as temporary:
        root = Path(temporary)
        bundle = root/'Application With Spaces/TapMesh.app'
        shutil.copytree(args.bundle, bundle, symlinks=True)
        assert not any(stat.S_IMODE(path.lstat().st_mode) & 0o022 for path in [bundle,*bundle.rglob('*')] if not path.is_symlink()), 'Bundle code/resources must deny group and other writes'
        checked('bundle code and resources deny group and other writes')
        resources = bundle/'Contents/Resources'
        python = resources/'runtime/bin/python3'
        profile = root/'Fresh Local Profile'
        legacy = root/'connection.json'
        legacy.write_text('{"private_environment":"preserve"}')
        env = {key: os.environ[key] for key in ('HOME', 'TMPDIR', 'LANG') if key in os.environ}
        env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
        code = 'import sys,agent,server,tapmesh_runtime,pymobiledevice3,idb; assert all(m.__file__.startswith(sys.prefix) for m in [agent,server,tapmesh_runtime,pymobiledevice3,idb]); print("bundled imports")'
        subprocess.run([str(python), '-I', '-B', '-c', code], cwd=root, env=env, check=True, timeout=15)
        checked('relocated interpreter and packages with system-only PATH')

        def start():
            return subprocess.Popen([str(python), '-I', '-B', '-m', 'tapmesh_runtime', 'supervise', '--state-dir', str(profile),
                '--resources', str(resources), '--parent-pid', str(os.getpid())], cwd=root, env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        process = start()
        configuration = None
        try:
            first = receive(process)
            assert first['owner'] is True
            configuration = json.loads((profile/'connection.json').read_text())
            assert json.loads(legacy.read_text()) == {'private_environment': 'preserve'}
            credentials = {name: (profile/name).read_bytes() for name in ('server.token', 'agent.token', 'approvals.token')}
            checked('fresh private local profile; legacy environment preserved')
            with httpx.Client(timeout=50, trust_env=False) as client:
                url = configuration['api_url']
                assert client.get(url+'/api/tools').status_code == 401
                headers = {'Authorization': 'Bearer '+credentials['server.token'].decode().strip()}
                assert len(client.get(url+'/api/tools', headers=headers).json()['tools']) == 50
                assert client.get(url+'/internal/ocr/health', headers=headers).status_code == 401
                assert client.get(url+'/api/approvals', headers=headers).status_code == 403
                checked('local HTTP authentication and owner/OCR credential separation')
                headers = {'Authorization': 'Bearer '+credentials['agent.token'].decode().strip()}
                health = client.get(url+'/internal/ocr/health', headers=headers).json()
                assert health['status'] == 'available' and health['engine'] == 'vision' and 'eng' in health['languages']
                from PIL import Image, ImageDraw, ImageFont
                image = Image.new('RGB', (1200, 400), 'white')
                ImageDraw.Draw(image).text((100, 90), 'TapMesh 12345', font=ImageFont.truetype('/System/Library/Fonts/Helvetica.ttc', 70), fill='black')
                output = io.BytesIO(); image.save(output, format='PNG')
                result = client.post(url+'/internal/ocr', headers=headers, json={'image_base64': base64.b64encode(output.getvalue()).decode(), 'logical_size': {'width': 600, 'height': 200}})
                assert result.status_code == 200, result.status_code
                assert result.json()['engine'] == 'vision'
                assert 'TapMesh' in ' '.join(row['label'] for row in result.json()['elements'])
                checked('bundled authenticated Vision OCR through local HTTP')
            duplicate = start()
            try:
                attached = receive(duplicate)
                assert attached['owner'] is False and attached['instance_id'] == first['instance_id']
                assert duplicate.wait(timeout=5) == 0
                assert process.poll() is None
            finally:
                if duplicate.poll() is None:
                    duplicate.terminate(); duplicate.wait(timeout=10)
            checked('duplicate launch attaches without stopping owned services')
            asyncio.run(mcp_check(bundle/'Contents/MacOS/tapmesh-mcp', profile, root, env))
            checked('relocated MCP stdio bridge ignores inherited HTTP proxy')
            process.terminate(); assert process.wait(timeout=50) == 0
            assert services_stopped(configuration)
            checked('graceful shutdown stops both owned listeners')
            process = start()
            second = receive(process)
            assert second['instance_id'] != first['instance_id']
            assert all((profile/name).read_bytes() == content for name, content in credentials.items())
            configuration = json.loads((profile/'connection.json').read_text())
            checked('restart changes instance while preserving local credentials')
        finally:
            if process.poll() is None:
                process.terminate(); process.wait(timeout=50)
            if configuration:
                assert services_stopped(configuration)
        subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(bundle)],check=True)
        checked('bundle signature preserved after runtime and MCP execution')
    report['status'] = 'passed'
    if args.report:
        args.report.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
