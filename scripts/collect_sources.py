#!/usr/bin/env python3
"""Retain checksum-verified dependency source archives for an actual app runtime.

Exit 0 means the report has no source gaps; exit 2 means an offline pack was
written but its source-closure review remains incomplete. Never executes sdists,
build hooks, installed packages, or the bundled Python interpreter.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import plistlib
from pathlib import Path
import re
import shutil
import threading
import subprocess
import tarfile
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / 'Library/Caches/TapMesh/source-archives'
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_LICENSE_BYTES = 2 * 1024 * 1024
MAX_LICENSE_TOTAL = 16 * 1024 * 1024
_COPY_LOCK = threading.Lock()
LICENSE_SUPPLEMENTS = {
    ('loguru', '0.7.3'): {'name': 'loguru-license', 'version': '0.7.3',
        'repository': 'Delgan/loguru', 'tag': '0.7.3',
        'tag_object': 'eb27ef8546577adbb88ad36b62b4eca9e9dae217',
        'commit': 'ae3bfd1b85b6b4a3db535f69b975687c79498be4',
        'sha256': 'b35d026cc7aca9d5859a02eb87ddf7a386a24c986838651bd1f283f94e003327',
        'path': 'LICENSE'},
}
# Assessed source layouts. Closure still requires real installed load commands,
# the preferred-language source members, build recipes, and all vendored sources.
SOURCE_LAYOUTS = {
    'backports-zstd': (('src/c/compression_zstd/', 'src/c/zstd/lib/'), 'C including vendored zstd'),
    'cffi': (('src/c/',), 'C; libffi is supplied by macOS'),
    'chardet': (('src/chardet/',), 'Python compiled by the declared mypyc build hook'),
    'charset-normalizer': (('src/charset_normalizer/',), 'Python/Cython sources'),
    'multidict': (('multidict/',), 'C/Cython and retained headers'),
    'protobuf': (('python/', 'upb/', 'utf8_range/'), 'C extension and retained upb/third-party source'),
    'psutil': (('psutil/',), 'C and Apple platform APIs'),
    'pycryptodome': (('src/',), 'C including retained bundled algorithm sources'),
    'pylzss': (('src/pylzss.c', 'src/include/'), 'C and retained LZSS headers'),
    'sslpsk-pmd3': (('sslpsk_pmd3/_sslpsk.c',), 'C; source binds interpreter OpenSSL without bundling it'),
    'wcwidth': (('libwcwidth/',), 'C and retained bundled libwcwidth source'),
}
# These commits were resolved through the official GitHub tag API. Archive
# hashes pin the actual retained source bytes; API evidence is also preserved.
UPSTREAM_SOURCES = (
    {'name': 'idb-companion', 'version': '1.7.8', 'repository': 'meta/idb',
     'tag': 'v1.7.8', 'commit': 'c793afad2a31092628f7013cf1aa96f266c85024',
     'sha256': 'c440cd8b4fa2b0adaf675990e5a7ec7b5d43b4a3fb574fd86d419c57aa295f41'},
    {'name': 'python-build-standalone', 'version': '20260510',
     'repository': 'astral-sh/python-build-standalone', 'tag': '20260510',
     'commit': 'f0ae464f527498d6c4b13f19977447d603cd5edd',
     'sha256': '47db35a39364102579b19d1d36de793bba0c127b92899d8652c9a0b6347ed0e0'},
    {'name': 'sparkle', 'version': '2.10.1', 'repository': 'sparkle-project/Sparkle',
     'tag': '2.10.1', 'commit': 'ab7d71291238df6e0492143b120c9b696a35f603',
     'sha256': 'd7e5599d56ddb917cddbb4a719fb534ad5d2d7e80a653446d0eea6a38920b302'},
    {'name': 'cpython', 'version': '3.12.13', 'repository': 'python/cpython',
     'tag': 'v3.12.13', 'tag_object': '082755cdf017defd58f2ff7ff0341569946d30e0',
     'commit': '3bb231a6a5dc02b95658877318bf61501a7209e9',
     'sha256': '0192a16a30223fc51b62f13e0b3d5bed007dc9de1322de3d0dd4106a8b83038c'},
)

PILLOW_SOURCES = ({'name': '2.3.3.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/2.3.3.tar.gz',
  'sha256': 'f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1'},
 {'name': 'brotli-1.2.0.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/brotli-1.2.0.tar.gz',
  'sha256': '816c96e8e8f193b40151dad7e8ff37b1221d019dbcb9c35cd3fadbfe6477dfec'},
 {'name': 'bzip2-1.0.8.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/bzip2-1.0.8.tar.gz',
  'sha256': 'ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269'},
 {'name': 'freetype-2.14.3.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/freetype-2.14.3.tar.gz',
  'sha256': 'e61b31ab26358b946e767ed7eb7f4bb2e507da1cfefeb7a8861ace7fd5c899a1'},
 {'name': 'harfbuzz-14.5.0.tar.xz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/harfbuzz-14.5.0.tar.xz',
  'sha256': 'b7132e148358a45185c9feafd049dbaf243649d3c44414b3534d9c95d18592b9'},
 {'name': 'lcms2-2.19.1.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/lcms2-2.19.1.tar.gz',
  'sha256': 'bfc54f7bab59fbc921012014a8032e4cba4abd46db47d46b76416a8c0b2815c8'},
 {'name': 'libXau-1.0.12.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libXau-1.0.12.tar.gz',
  'sha256': '2402dd938da4d0a332349ab3d3586606175e19cb32cb9fe013c19f1dc922dcee'},
 {'name': 'libavif-1.4.2.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libavif-1.4.2.tar.gz',
  'sha256': '2b645287340ba5a631d268b551dc2d72bd73ac33335962dd36dcdb6d8366921d'},
 {'name': 'libjpeg-turbo-3.2.0.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libjpeg-turbo-3.2.0.tar.gz',
  'sha256': '6f30092cef9fb839779646608f4ee14ae3cbac989c47fa05e841b0841f09878e'},
 {'name': 'libpng-1.6.58.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libpng-1.6.58.tar.gz',
  'sha256': 'a9d4df463d36a6e5f9c29bd6f4967312d17e996c1854f3511f833924eb1993cf'},
 {'name': 'libpthread-stubs-0.5.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libpthread-stubs-0.5.tar.gz',
  'sha256': '593196cc746173d1e25cb54a93a87fd749952df68699aab7e02c085530e87747'},
 {'name': 'libwebp-1.6.0.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libwebp-1.6.0.tar.gz',
  'sha256': 'e4ab7009bf0629fd11982d4c2aa83964cf244cffba7347ecd39019a9e38c4564'},
 {'name': 'libxcb-1.17.0.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/libxcb-1.17.0.tar.gz',
  'sha256': '2c69287424c9e2128cb47ffe92171e10417041ec2963bceafb65cb3fcf8f0b85'},
 {'name': 'openjpeg-2.5.4.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/openjpeg-2.5.4.tar.gz',
  'sha256': 'a695fbe19c0165f295a8531b1e4e855cd94d0875d2f88ec4b61080677e27188a'},
 {'name': 'pkg-config-0.29.2.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/pkg-config-0.29.2.tar.gz',
  'sha256': '6fc69c01688c9458a57eb9a1664c9aba372ccda420a02bf4429fe610e7e7d591'},
 {'name': 'tiff-4.7.2.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/tiff-4.7.2.tar.gz',
  'sha256': '672bd7d10aee4606171afb864f3570b83340f6a33e2c186dc0512f7145ffdf6a'},
 {'name': 'xcb-proto-1.17.0.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/xcb-proto-1.17.0.tar.gz',
  'sha256': '392d3c9690f8c8202a68fdb89c16fd55159ab8d65000a6da213f4a1576e97a16'},
 {'name': 'xorgproto-2025.1.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/xorgproto-2025.1.tar.gz',
  'sha256': 'd6f89f65bafb8c9b735e0515882b8a1511e8e864dde5e9513e191629369f2256'},
 {'name': 'xz-5.8.4.tar.gz',
  'url': 'https://raw.githubusercontent.com/python-pillow/pillow-depends/70c5bf5e940f5f1aee75ddb0421f0abfd9581261/xz-5.8.4.tar.gz',
  'sha256': '0014c7886930454fe8bd4228665b51af55eeae560ea135c9c4cd33f55b2591d9'},
 {'name': 'Pillow-13.0.0-build-recipes.tar.gz',
  'url': 'https://codeload.github.com/python-pillow/Pillow/tar.gz/293218ad059d994baf1777d882911bed6a49704a',
  'sha256': '0458511ac96e7d44e574484efff9c6210f8211046b3e7ec1dca2381cea0f8430'},
 {'name': 'aom-3.14.1.tar.gz',
  'url': 'https://aomedia.googlesource.com/aom/+archive/refs/tags/v3.14.1.tar.gz',
  'sha256': 'b4be5e6f593fba1c3f8031ebbbdb658bdf95f7db941fb427cc45b4f56d84a028'},
 {'name': 'dav1d-1.5.3.tar.gz',
  'url': 'https://code.videolan.org/videolan/dav1d/-/archive/1.5.3/dav1d-1.5.3.tar.gz',
  'sha256': 'cbe212b02faf8c6eed5b6d55ef8a6e363aaab83f15112e960701a9c3df813686'},
 {'name': 'libyuv-644251f.tar.gz',
  'url': 'https://chromium.googlesource.com/libyuv/libyuv/+archive/644251f252a84bf8ce91ff0aca86a9b16b069ab8.tar.gz',
  'sha256': '165d748bfdf0beb42bc3ca39b98776d6dc40d91b29194f96a34674e473e8a38e'},
 {'name': 'multibuild-c893f305.tar.gz',
  'url': 'https://codeload.github.com/multi-build/multibuild/tar.gz/c893f305c6066b6d940e10d3333be8cb27fa3947',
  'sha256': 'e50c410a625333404ba318f109b082ff04b1edb1e18e92109e38897c9561db09'})


class CollectionError(Exception):
    """A bounded, public build error that contains no backend output."""


def sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def normalized(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def safe_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._+-]', '_', name)[:180]


def fetch(url: str, destination: Path, *, limit: int = MAX_ARCHIVE_BYTES) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname:
        raise CollectionError('source_url_must_use_https')
    # macOS curl uses the system trust store. Do not disable certificate checks
    # when a build-time Python distribution lacks its own CA bundle.
    command = ['/usr/bin/curl', '--fail', '--silent', '--show-error', '--location',
               '--proto', '=https', '--proto-redir', '=https', '--connect-timeout', '15',
               '--max-time', '180', '--retry', '2', '--max-filesize', str(limit),
               '--user-agent', 'TapMesh-source-collector/0.1', '--output', str(destination), url]
    try:
        result = subprocess.run(command, capture_output=True, timeout=200)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CollectionError('source_download_failed') from exc
    if result.returncode or not destination.is_file() or destination.stat().st_size > limit:
        raise CollectionError('source_download_failed')


def cached_archive(url: str, expected: str, cache: Path, *, offline: bool) -> Path:
    if not re.fullmatch('[a-f0-9]{64}', expected):
        raise CollectionError('source_hash_missing')
    filename = safe_name(unquote(Path(urlsplit(url).path).name))
    path = cache / f'{expected}-{filename}'
    if path.is_file() and sha256(path) == expected:
        return path
    if offline:
        raise CollectionError('source_not_cached_or_checksum_mismatch')
    with tempfile.NamedTemporaryFile(prefix='source-', suffix='.download', dir=cache, delete=False) as temporary:
        download = Path(temporary.name)
    try:
        fetch(url, download)
        if sha256(download) != expected:
            raise CollectionError('source_checksum_mismatch')
        download.replace(path)
    finally:
        download.unlink(missing_ok=True)
    return path


def license_member(name: str) -> bool:
    parts = Path(name).parts
    basename = parts[-1].lower() if parts else ''
    return (any(part.lower() in {'licenses', 'licences'} for part in parts[:-1])
            or bool(re.match(r'^(licen[sc]e|copying|notice|copyright)([._-].*)?$', basename)))


def embedded_license(data: bytes) -> bool:
    text = ' '.join(data.decode('utf-8', 'replace').lower().split())
    return (("permission is hereby granted, free of charge" in text
             and 'the software is provided "as is"' in text)
            or ('apache license' in text and 'end of terms and conditions' in text)
            or ('gnu general public license' in text and 'how to apply these terms' in text)
            or ('redistribution and use in source and binary forms' in text
                and 'this software is provided' in text))


def license_candidate(name: str) -> bool:
    return license_member(name) or Path(name).name.lower().startswith(('readme', 'pkg-info', 'metadata'))


def archive_licenses(archive: Path, destination: Path) -> list[dict]:
    """Read bounded license members; never extract an untrusted archive path."""
    records = []
    total = 0

    def retain(name, size, read):
        nonlocal total
        if not license_candidate(name) or not 0 < size <= MAX_LICENSE_BYTES:
            return
        if len(records) >= 128 or total + size > MAX_LICENSE_TOTAL:
            return
        data = read(MAX_LICENSE_BYTES + 1)
        if len(data) != size:
            raise CollectionError('source_license_member_invalid')
        if not license_member(name) and not embedded_license(data):
            return
        digest = hashlib.sha256(data).hexdigest()
        target = destination / f'{digest[:12]}-{safe_name(Path(name).name)}'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        total += len(data)
        records.append({'archive_member': name, 'file': target.name, 'sha256': digest, 'bytes': len(data)})

    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as source:
                for member in source.infolist():
                    if member.is_dir() or not license_candidate(member.filename):
                        continue
                    with source.open(member) as stream:
                        retain(member.filename, member.file_size, stream.read)
        else:
            with tarfile.open(archive, 'r:*') as source:
                for member in source:
                    if member.isfile() and license_candidate(member.name):
                        stream = source.extractfile(member)
                        if stream is not None:
                            with stream:
                                retain(member.name, member.size, stream.read)
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise CollectionError('source_archive_invalid') from exc
    return records


def installed_licenses(distribution, site: Path, destination: Path) -> list[dict]:
    records = []
    for file in distribution.files or []:
        if not license_candidate(str(file)):
            continue
        origin = Path(distribution.locate_file(file)).resolve()
        if not origin.is_relative_to(site) or not origin.is_file() or not 0 < origin.stat().st_size <= MAX_LICENSE_BYTES:
            continue
        if not license_member(str(file)) and not embedded_license(origin.read_bytes()):
            continue
        digest = sha256(origin)
        name = f'{digest[:12]}-{safe_name(origin.name)}'
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination / name)
        records.append({'installed_member': str(file), 'file': name, 'sha256': digest, 'bytes': origin.stat().st_size})
    return records


def native_members(distribution) -> list[str]:
    return sorted(str(file) for file in distribution.files or []
                  if str(file).endswith(('.so', '.dylib', '.a', '.dll', '.pyd'))
                  and Path(distribution.locate_file(file)).is_file())


def copy_archive(archive: Path, destination: Path, original_url: str) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    name = safe_name(unquote(Path(urlsplit(original_url).path).name))
    # Commit-based GitHub archives need a descriptive retained filename.
    if not name.endswith(('.tar.gz', '.tar.bz2', '.tar.xz', '.zip', '.tgz', '.crate')):
        name += '.tar.gz'
    target = destination / name
    expected = sha256(archive)
    cache_prefix = archive.name.split('-', 1)[0]
    if re.fullmatch('[a-f0-9]{64}', cache_prefix) and cache_prefix != expected:
        raise CollectionError('source_cache_changed_during_copy')
    if not target.is_file() or sha256(target) != expected:
        shutil.copyfile(archive, target)
    if sha256(target) != expected:
        raise CollectionError('retained_source_checksum_mismatch')
    return {'file': str(target), 'sha256': expected, 'bytes': target.stat().st_size}


def verify_tag(entry: dict, cache: Path, *, offline: bool) -> dict:
    cache_file = cache / f"tag-{safe_name(entry['repository'])}-{safe_name(entry['tag'])}.json"
    url = f"https://api.github.com/repos/{entry['repository']}/git/ref/tags/{entry['tag']}"
    if not offline and not cache_file.is_file():
        with tempfile.NamedTemporaryFile(prefix='tag-', dir=cache, delete=False) as temporary:
            downloaded = Path(temporary.name)
        try:
            fetch(url, downloaded, limit=1024 * 1024)
            value = json.loads(downloaded.read_text())
            expected = entry.get('tag_object', entry['commit'])
            if value.get('object', {}).get('sha') != expected:
                raise CollectionError('upstream_tag_commit_mismatch')
            chain = [value]
            if 'tag_object' in entry:
                fetch(f"https://api.github.com/repos/{entry['repository']}/git/tags/{entry['tag_object']}", downloaded, limit=1024 * 1024)
                value = json.loads(downloaded.read_text())
                if value.get('object', {}).get('sha') != entry['commit']:
                    raise CollectionError('upstream_tag_commit_mismatch')
                chain.append(value)
            cache_file.write_text(json.dumps({'url': url, 'commit': entry['commit'], 'responses': chain}, indent=2) + '\n')
        finally:
            downloaded.unlink(missing_ok=True)
    if not cache_file.is_file():
        raise CollectionError('upstream_tag_evidence_not_cached')
    result = json.loads(cache_file.read_text())
    responses = result.get('responses', [])
    expected = entry.get('tag_object', entry['commit'])
    if (result.get('commit') != entry['commit'] or not responses
            or responses[0].get('object', {}).get('sha') != expected
            or ('tag_object' in entry and (len(responses) != 2
                or responses[1].get('object', {}).get('sha') != entry['commit']))):
        raise CollectionError('upstream_tag_commit_mismatch')
    return result


def archive_inventory(archive: Path) -> tuple[list[str], dict[str, bytes]]:
    names, texts = [], {}
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            if not member.isfile():
                continue
            names.append(member.name)
            basename = Path(member.name).name
            if basename in {'Cargo.lock', 'Cargo.toml', 'setup.py', 'setup.cfg', 'pyproject.toml', 'CMakeLists.txt'} and member.size <= 4 * 1024 * 1024:
                stream = source.extractfile(member)
                if stream is not None:
                    texts[member.name] = stream.read()
    return names, texts


def macho_evidence(path: Path, relative: str) -> dict:
    result = {'file': relative, 'sha256': sha256(path), 'load_libraries': [], 'rpaths': []}
    with path.open('rb') as stream:
        magic = stream.read(4)
    if magic not in {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}:
        result['format'] = 'windows-pe' if magic.startswith(b'MZ') else 'other-native'
        return result
    result['format'] = 'mach-o'
    try:
        output = subprocess.run(['/usr/bin/otool', '-l', str(path)], capture_output=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectionError('native_load_commands_unavailable') from exc
    if len(output) > 4 * 1024 * 1024:
        raise CollectionError('native_load_commands_exceed_limit')
    command = None
    for line in output.decode('utf-8', 'replace').splitlines():
        line = line.strip()
        if line.startswith('cmd '):
            command = line.removeprefix('cmd ')
        elif command in {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LAZY_LOAD_DYLIB', 'LC_LOAD_UPWARD_DYLIB'} and line.startswith('name '):
            result['load_libraries'].append(line[5:].split(' (offset ')[0])
        elif command == 'LC_RPATH' and line.startswith('path '):
            result['rpaths'].append(line[5:].split(' (offset ')[0])
    result['load_libraries'] = sorted(set(result['load_libraries']))
    result['rpaths'] = sorted(set(result['rpaths']))
    result['non_system_load_libraries'] = [name for name in result['load_libraries']
                                           if not name.startswith(('/usr/lib/', '/System/Library/'))]
    return result


def canonical_apache(cache, offline):
    return cached_archive('https://www.apache.org/licenses/LICENSE-2.0.txt',
                          'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30', cache, offline=offline)


def collect_rust_sources(lock_data: bytes, destination: Path, cache: Path, offline: bool) -> dict:
    lock = tomllib.loads(lock_data.decode())
    result = {'crates': [], 'gaps': []}
    for package in lock.get('package', []):
        origin = package.get('source')
        if not origin:
            continue  # Workspace/path members are already inside the parent sdist.
        name, version = package['name'], package['version']
        record = {'name': name, 'version': version, 'source': origin}
        if origin != 'registry+https://github.com/rust-lang/crates.io-index' or not package.get('checksum'):
            record['gap'] = 'cargo_nonregistry_source_not_retained'
        else:
            url = f'https://static.crates.io/crates/{name}/{name}-{version}.crate'
            directory = destination / 'rust-crates' / f'{safe_name(name)}-{safe_name(version)}'
            try:
                archive = cached_archive(url, package['checksum'], cache, offline=offline)
                # Several parent lockfiles can use the same crate concurrently.
                with _COPY_LOCK:
                    source = copy_archive(archive, directory, url)
                    licenses = archive_licenses(archive, directory / 'licenses')
                source['file'] = str(Path(source['file']).relative_to(destination))
                for item in licenses:
                    item['file'] = str(directory.relative_to(destination) / 'licenses' / item['file'])
                record.update(archive=source, licenses=licenses, checksum_verified=True)
                if not licenses:
                    with tarfile.open(archive, 'r:*') as source_archive:
                        cargo = next(member for member in source_archive if member.name == f'{name}-{version}/Cargo.toml')
                        crate_metadata = tomllib.loads(source_archive.extractfile(cargo).read().decode())['package']
                    declaration = crate_metadata.get('license', '')
                    alternatives = [branch.strip(' ()') for branch in declaration.split(' OR ')]
                    if 'Apache-2.0' in alternatives or declaration == 'MIT/Apache-2.0':
                        canonical = canonical_apache(cache, offline)
                        license_path = directory / 'licenses' / 'Apache-2.0-canonical.txt'
                        with _COPY_LOCK:
                            license_path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(canonical, license_path)
                        record['license_declaration'] = declaration
                        record['selected_license'] = 'Apache-2.0'
                        record['licenses'].append({'file': str(license_path.relative_to(destination)), 'sha256': sha256(license_path),
                                                  'source_url': 'https://www.apache.org/licenses/LICENSE-2.0.txt',
                                                  'provenance': 'Canonical text for an explicit package-declared license alternative; copyright and attribution remain in the retained source archive.'})
                    else:
                        record['gap'] = 'crate_license_text_missing' 
            except (CollectionError, OSError, ValueError) as exc:
                record['gap'] = str(exc) if isinstance(exc, CollectionError) else 'crate_collection_failed'
        if record.get('gap'):
            result['gaps'].append({'name': name, 'version': version, 'gap': record['gap']})
        result['crates'].append(record)
    return result


def collect_pillow_sources(record, directory, destination, cache, offline, binaries, foreign):
    result = {'archives': [], 'gaps': [], 'release_commit': '293218ad059d994baf1777d882911bed6a49704a',
              'mirror_commit': '70c5bf5e940f5f1aee75ddb0421f0abfd9581261',
              'recipe_members': ['.github/dependencies.json', '.github/workflows/wheels-dependencies.sh', 'pyproject.toml', 'wheels/multibuild/'],
              'binary_version_qualification': {'freetype': '2.14.3', 'harfbuzz': '14.5.0', 'libpng': '1.6.58', 'jpegturbo': '3.2.0',
                  'lcms2': '2.19', 'libavif': '1.4.2', 'libwebp': '1.6.0', 'openjpeg': '2.5.4', 'tiff': '4.7.2', 'xz': '5.8.4', 'zlib-ng': '2.3.3', 'aom': '3.14.1', 'dav1d': '1.5.3-0-gb546257'},
              'qualification_scope': 'Exact locked CPython3.12 macOS arm64 Pillow13.0.0 wheel; system library version APIs were queried during qualification, never by this collector.'}
    if record['version'] != '13.0.0':
        result['gaps'].append({'code': 'pillow_release_version_unassessed', 'version': record['version']})
        return result
    known = {'libXau.6.dylib', 'libavif.16.4.2.dylib', 'libbrotlicommon.1.2.0.dylib', 'libbrotlidec.1.2.0.dylib',
             'libfreetype.6.dylib', 'libharfbuzz.0.dylib', 'libjpeg.62.4.0.dylib', 'liblcms2.2.dylib', 'liblzma.5.dylib',
             'libopenjp2.2.5.4.dylib', 'libpng16.16.dylib', 'libsharpyuv.0.dylib', 'libtiff.6.dylib', 'libwebp.7.dylib',
             'libwebpdemux.2.dylib', 'libwebpmux.3.dylib', 'libxcb.1.1.0.dylib', 'libz.1.3.1.zlib-ng.dylib'}
    unknown = sorted({Path(name).name for name in foreign} - known)
    if unknown:
        result['gaps'].append({'code': 'pillow_bundled_library_unassessed', 'libraries': unknown})
    for pin in PILLOW_SOURCES:
        target = directory / 'native-dependencies' / safe_name(pin['name'])
        try:
            archive = cached_archive(pin['url'], pin['sha256'], cache, offline=offline)
            retained = copy_archive(archive, target, pin['url'])
            retained['file'] = str(Path(retained['file']).relative_to(destination))
            licenses = archive_licenses(archive, target / 'licenses')
            for item in licenses:
                item['file'] = str(target.relative_to(destination) / 'licenses' / item['file'])
            component = dict(pin, archive=retained, licenses=licenses)
            if pin['name'] == 'Pillow-13.0.0-build-recipes.tar.gz':
                with tarfile.open(archive) as source:
                    member = next(member for member in source if member.name.endswith('/.github/dependencies.json'))
                    result['declared_dependency_versions'] = json.loads(source.extractfile(member).read())
            if not licenses:
                result['gaps'].append({'code': 'pillow_dependency_license_text_missing', 'component': pin['name']})
            result['archives'].append(component)
        except (CollectionError, OSError, ValueError) as exc:
            result['gaps'].append({'code': str(exc) if isinstance(exc, CollectionError) else 'pillow_source_collection_failed', 'component': pin['name']})
    (directory / 'PILLOW_SOURCE_BUILD.txt').write_text(
        'Pillow13 macOS arm64 source build inputs\n\n'
        'The retained release source contains .github/dependencies.json and the wheel dependency build script.\n'
        'Restore the retained multibuild snapshot under wheels/multibuild (the upstream git submodule path).\n'
        'Place the retained mirror archives, using their original archive names, into build/darwin/pillow-depends-main; '
        'this satisfies the script local archive cache before it attempts a network download.\n'
        'For libavif, restore the retained AOM, DAV1D and libyuv source trees under its ext/aom, ext/dav1d and ext/libyuv paths '
        'before configuring the LOCAL codec targets. libsharpyuv is supplied by retained libwebp1.6.0.\n'
        'Use the exact build flags in .github/workflows/wheels-dependencies.sh and pyproject.toml, including '
        'AVIF AOM encoder, DAV1D decoder, local libyuv/sharpyuv, and the macOS FreeType/HarfBuzz rebuild cycle.\n'
        'The build needs Apple clang/SDK and the upstream declared Python, CMake, Meson, Ninja and pkg-config build tools. '
        'This pack retains preferred sources/build recipes; binary reproduction was not independently performed.\n')
    return result


def native_audit(distribution, record, archive, directory, destination, cache, offline):
    members, texts = archive_inventory(archive)
    source_members = sorted(name for name in members if Path(name).suffix.lower() in {'.py', '.pyx', '.pxd', '.c', '.h', '.cpp', '.hpp', '.rs', '.s'})
    recipes = sorted(texts)
    binaries = [macho_evidence(Path(distribution.locate_file(name)), name) for name in record['native_members']]
    audit = {'name': record['name'], 'version': record['version'], 'source_archive_sha256': sha256(archive),
             'preferred_source_members': source_members, 'build_recipe_members': recipes,
             'binaries': binaries, 'gaps': [], 'source_closure_verified': False,
             'binary_reproduction_verified': False}
    key = normalized(record['name'])
    foreign = sorted({library for binary in binaries for library in binary.get('non_system_load_libraries', [])})
    non_macho = [binary['file'] for binary in binaries if binary['format'] != 'mach-o']
    if foreign and key != 'pillow':
        audit['gaps'].append({'code': 'bundled_native_library_sources_missing', 'libraries': foreign})
    if non_macho:
        audit['gaps'].append({'code': 'non_macos_binary_sources_unverified', 'files': non_macho})
    root = members[0].split('/')[0] + '/' if members else ''
    relative_sources = [name.removeprefix(root) for name in source_members]
    key = normalized(record['name'])
    lock_members = sorted((name for name in texts if name.endswith('/Cargo.lock')), key=len)
    if lock_members:
        audit['cargo_lock_member'] = lock_members[0]
        audit['rust_sources'] = collect_rust_sources(texts[lock_members[0]], destination, cache, offline)
        if audit['rust_sources']['gaps']:
            audit['gaps'].append({'code': 'cargo_dependency_sources_or_licenses_missing', 'dependencies': audit['rust_sources']['gaps']})
        if key == 'cryptography':
            versions = set()
            for binary in binaries:
                data = Path(distribution.locate_file(binary['file'])).read_bytes()
                versions.update(value.decode() for value in re.findall(rb'OpenSSL ([0-9]+\.[0-9]+\.[0-9]+) [0-9]{1,2} [A-Za-z]+ [0-9]{4}', data))
            audit['embedded_openssl_versions'] = sorted(versions)
            if versions == {'4.0.3'}:
                url = 'https://github.com/openssl/openssl/releases/download/openssl-4.0.3/openssl-4.0.3.tar.gz'
                expected = '325b5c806167c13b40b1ffeadfe0248197c00eccc4cf123ec1e28d2d2fd216d9'
                try:
                    openssl = cached_archive(url, expected, cache, offline=offline)
                    openssl_dir = directory / 'native-dependencies' / 'openssl-4.0.3'
                    retained = copy_archive(openssl, openssl_dir, url)
                    retained['file'] = str(Path(retained['file']).relative_to(destination))
                    retained['url'] = url
                    retained['upstream_checksum_url'] = url + '.sha256'
                    checksum_text = cached_archive(url + '.sha256', 'ffded23b33b94d84ba776ba925b1879d7d4dfbf944a9fa74c1f72b474796a8a6', cache, offline=offline)
                    shutil.copyfile(checksum_text, openssl_dir / 'upstream-sha256.txt')
                    retained['checksum_evidence_file'] = str((openssl_dir / 'upstream-sha256.txt').relative_to(destination))
                    licenses = archive_licenses(openssl, openssl_dir / 'licenses')
                    for item in licenses:
                        item['file'] = str(openssl_dir.relative_to(destination) / 'licenses' / item['file'])
                    audit['embedded_openssl_source'] = {'version': '4.0.3', 'archive': retained, 'licenses': licenses,
                        'build_recipe_members': [member for member in recipes if member.endswith(('Cargo.toml', 'pyproject.toml', 'build.rs'))],
                        'upstream_build_instructions': ['openssl-4.0.3/INSTALL.md', 'openssl-4.0.3/Configurations/'],
                        'parent_build_instructions': 'cryptography-50.0.2/docs/installation.rst'}
                    if not licenses:
                        audit['gaps'].append({'code': 'openssl_license_text_missing', 'version': '4.0.3'})
                except (CollectionError, OSError) as exc:
                    audit['gaps'].append({'code': str(exc) if isinstance(exc, CollectionError) else 'openssl_source_collection_failed', 'version': '4.0.3'})
            else:
                audit['gaps'].append({'code': 'statically_embedded_openssl_source_version_unverified', 'versions': sorted(versions)})
        elif not any(name.endswith('.rs') for name in source_members):
            audit['gaps'].append({'code': 'preferred_rust_source_members_missing'})
    elif key == 'pillow':
        audit['pillow_sources'] = collect_pillow_sources(record, directory, destination, cache, offline, binaries, foreign)
        audit['gaps'].extend(audit['pillow_sources']['gaps'])
    elif key in SOURCE_LAYOUTS:
        prefixes, description = SOURCE_LAYOUTS[key]
        audit['assessment'] = description
        absent = [prefix for prefix in prefixes if not any(name.startswith(prefix) for name in relative_sources)]
        if absent:
            audit['gaps'].append({'code': 'assessed_preferred_source_layout_missing', 'paths': absent})
    else:
        audit['gaps'].append({'code': 'preferred_native_source_layout_unassessed'})
    if not recipes:
        audit['gaps'].append({'code': 'native_build_recipe_missing'})
    audit['source_closure_verified'] = not audit['gaps']
    path = directory / 'native-source-evidence.json'
    path.write_text(json.dumps(audit, indent=2) + '\n')
    return {'file': str(path.relative_to(destination)), 'sha256': sha256(path),
            'source_closure_verified': audit['source_closure_verified'], 'gaps': audit['gaps']}


def supplement_license(record, directory, cache, offline):
    entry = LICENSE_SUPPLEMENTS.get((normalized(record['name']), record['version']))
    if entry is None:
        if any('GNU General Public License v3 or later' in value for value in record.get('license_classifiers', [])):
            canonical = ROOT / 'LICENSE'
            expected = '8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903'
            if sha256(canonical) != expected:
                raise CollectionError('canonical_gpl_text_checksum_mismatch')
            output = directory / 'licenses-source' / 'GPL-3.0-canonical.txt'
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical, output)
            record.setdefault('source_licenses', []).append({'file': output.name, 'sha256': expected, 'bytes': output.stat().st_size,
                'provenance': 'Canonical GNU GPL3 text retained for the exact distribution metadata declaration GPLv3-or-later; existing attribution remains in the retained source.'})
        return
    evidence = verify_tag(entry, cache, offline=offline)
    url = f"https://raw.githubusercontent.com/{entry['repository']}/{entry['commit']}/{entry['path']}"
    cached = cached_archive(url, entry['sha256'], cache, offline=offline)
    output = directory / 'licenses-source' / f"{entry['sha256'][:12]}-LICENSE-upstream"
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, output)
    (directory / 'license-tag-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    record.setdefault('source_licenses', []).append({'file': output.name, 'sha256': entry['sha256'], 'bytes': output.stat().st_size,
                                                  'source_url': url, 'upstream_commit': entry['commit']})


def collect_package(distribution, locked, site, destination, cache, offline):
    name, version = distribution.metadata['Name'], distribution.version
    directory = destination / 'packages' / f'{safe_name(normalized(name))}-{safe_name(version)}'
    directory.mkdir(parents=True, exist_ok=True)
    record = {'name': name, 'version': version, 'directory': str(directory.relative_to(destination)), 'license_expression': distribution.metadata.get('License-Expression'),
              'license_classifiers': [x for x in distribution.metadata.get_all('Classifier', []) if x.startswith('License ::')],
              'legacy_license': distribution.metadata.get('License'), 'gaps': []}
    record['installed_licenses'] = installed_licenses(distribution, site, directory / 'licenses-installed')
    metadata = distribution.read_text('METADATA') or ''
    (directory / 'METADATA.txt').write_text(metadata)
    record['native_members'] = native_members(distribution)
    if not record['native_members']:
        (directory / 'native-source-evidence.json').unlink(missing_ok=True)
    candidates = locked.get((normalized(name), version), [])
    if len(candidates) != 1:
        record['gaps'].append('exact_unique_lock_entry_missing')
    elif not candidates[0].get('sdist'):
        record['gaps'].append('exact_sdist_missing_in_lock')
    else:
        sdist = candidates[0]['sdist']
        try:
            expected = sdist['hash'].removeprefix('sha256:')
            archive = cached_archive(sdist['url'], expected, cache, offline=offline)
            record['source'] = copy_archive(archive, directory, sdist['url'])
            record['source']['url'] = sdist['url']
            record['source']['lock_hash_verified'] = True
            record['source_licenses'] = archive_licenses(archive, directory / 'licenses-source')
            if record['native_members']:
                record['native_source_evidence'] = native_audit(distribution, record, archive, directory, destination, cache, offline)
                if not record['native_source_evidence']['source_closure_verified']:
                    record['gaps'].append('native_source_closure_incomplete')
        except (CollectionError, KeyError, ValueError) as exc:
            record['gaps'].append(str(exc) if isinstance(exc, CollectionError) else 'invalid_sdist_lock_metadata')
    if not record['installed_licenses'] and not record.get('source_licenses'):
        try:
            supplement_license(record, directory, cache, offline)
        except (CollectionError, OSError, ValueError) as exc:
            record['gaps'].append(str(exc) if isinstance(exc, CollectionError) else 'license_supplement_failed')
    if not record['installed_licenses'] and not record.get('source_licenses'):
        record['gaps'].append('license_text_missing')
    return record


def collect_upstream(entry, destination, cache, offline, resources):
    directory = destination / 'upstream' / f"{entry['name']}-{entry['version']}"
    directory.mkdir(parents=True, exist_ok=True)
    record = dict(entry, directory=str(directory.relative_to(destination)), gaps=[])
    try:
        evidence = verify_tag(entry, cache, offline=offline)
        (directory / 'tag-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
        record['tag_commit_verified'] = True
        url = f"https://codeload.github.com/{entry['repository']}/tar.gz/{entry['commit']}"
        archive = cached_archive(url, entry['sha256'], cache, offline=offline)
        record['source'] = copy_archive(archive, directory, url)
        record['source']['url'] = url
        record['source_licenses'] = archive_licenses(archive, directory / 'licenses-source')
        if not record['source_licenses']:
            record['gaps'].append('license_text_missing')
        if entry['name'] == 'sparkle':
            members, _ = archive_inventory(archive)
            expected = ['/Vendor/ed25519-sparkle/src/sign.c', '/Vendor/bsdiff/bspatch.c', '/Sparkle.xcodeproj/project.pbxproj']
            absent = [suffix for suffix in expected if not any(name.endswith(suffix) for name in members)]
            if absent:
                record['gaps'].append('sparkle_preferred_sources_or_build_project_missing')
            framework = resources.parent / 'Frameworks/Sparkle.framework'
            native = []
            for file in framework.rglob('*'):
                if file.is_file() and not file.is_symlink():
                    with file.open('rb') as stream:
                        if stream.read(4) in {b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}:
                            native.append(macho_evidence(file, str(file.relative_to(framework))))
            external = sorted({name for binary in native for name in binary.get('non_system_load_libraries', [])
                               if 'Sparkle.framework/' not in name})
            if not native or external:
                record['gaps'].append('sparkle_native_dependency_sources_unassessed')
            audit = {'version': entry['version'], 'preferred_source_members': [name for name in members if Path(name).suffix in {'.m', '.mm', '.c', '.h', '.swift'}],
                     'build_project': [name for name in members if name.endswith(('project.pbxproj', '.xcconfig'))],
                     'binaries': native, 'external_dependencies': external, 'source_closure_verified': not record['gaps'], 'binary_reproduction_verified': False}
            (directory / 'native-source-evidence.json').write_text(json.dumps(audit, indent=2) + '\n')
            record['native_source_evidence'] = str((directory / 'native-source-evidence.json').relative_to(destination))
    except (CollectionError, ValueError, OSError) as exc:
        record['gaps'].append(str(exc) if isinstance(exc, CollectionError) else 'upstream_evidence_invalid')
    if entry['name'] in {'idb-companion', 'python-build-standalone'}:
        record['gaps'].append('native_transitive_source_closure_unverified')
    return record


def import_native_evidence(manifest: Path, destination: Path, upstream: list[dict]) -> dict:
    manifest = manifest.resolve(strict=True)
    evidence = json.loads(manifest.read_text())
    if evidence.get('schema_version') != 1 or not isinstance(evidence.get('files'), list) or len(evidence['files']) > 10000:
        raise CollectionError('native_review_manifest_invalid')
    known = {(entry['name'], entry['version']): entry for entry in upstream}
    inventory = {}
    folder = destination / 'upstream-native'
    for item in evidence['files']:
        relative = Path(item['file'])
        if relative.is_absolute() or not relative.parts or any(part in {'.', '..'} for part in relative.parts):
            raise CollectionError('native_review_path_invalid')
        original = (manifest.parent / relative).resolve(strict=True)
        if not original.is_relative_to(manifest.parent) or not original.is_file():
            raise CollectionError('native_review_path_invalid')
        if original.stat().st_size != item['bytes'] or sha256(original) != item['sha256']:
            raise CollectionError('native_review_checksum_mismatch')
        target = folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or sha256(target) != item['sha256']:
            shutil.copyfile(original, target)
        if sha256(target) != item['sha256']:
            raise CollectionError('retained_native_review_checksum_mismatch')
        inventory[str(relative)] = item
    copied = folder / 'native-dependency-review.json'
    shutil.copyfile(manifest, copied)
    for component in evidence.get('components', []):
        key = (component.get('name'), component.get('version'))
        if key not in known:
            raise CollectionError('native_review_component_version_mismatch')
        files = component.get('files', [])
        if not files or any(file not in inventory for file in files):
            raise CollectionError('native_review_component_files_missing')
        record = known[key]
        record['native_source_review'] = {'file': str(copied.relative_to(destination)), 'sha256': sha256(copied),
                                         'component': component['name'], 'source_closure_verified': component.get('source_closure_verified') is True,
                                         'verification_scope': evidence.get('verification_scope'), 'provenance': evidence.get('provenance')}
        if component.get('source_closure_verified') is True and component.get('gaps') == []:
            record['gaps'] = [gap for gap in record['gaps'] if gap != 'native_transitive_source_closure_unverified']
        else:
            record['gaps'].append('native_source_review_incomplete')
    return {'file': str(copied.relative_to(destination)), 'sha256': sha256(copied), 'files_verified': len(inventory),
            'source_archives': sum(item.get('role') == 'source_archive' for item in inventory.values()),
            'provenance': evidence.get('provenance')}


def collect(resources: Path, destination: Path, *, cache: Path = CACHE, offline: bool = False, native_evidence: Path | None = None) -> dict:
    resources = resources.resolve(strict=True)
    destination = destination.resolve()
    if destination == resources or resources.is_relative_to(destination):
        raise CollectionError('destination_must_not_contain_resources')
    manifest_path = resources / 'runtime-manifest.json'
    runtime_manifest = json.loads(manifest_path.read_text())
    if (runtime_manifest.get('python', {}).get('version') != '3.12.13'
            or runtime_manifest.get('python', {}).get('build') != '20260510'
            or runtime_manifest.get('companion', {}).get('version') != '1.7.8'):
        raise CollectionError('unqualified_runtime_source_versions')
    sites = list((resources / 'runtime/lib').glob('python*/site-packages'))
    if len(sites) != 1:
        raise CollectionError('one_runtime_site_packages_required')
    site = sites[0].resolve()
    distributions = list(importlib.metadata.distributions(path=[str(site)]))
    if any(not d.metadata.get('Name') or not d.version for d in distributions):
        raise CollectionError('runtime_distribution_metadata_invalid')
    distributions.sort(key=lambda x: normalized(x.metadata['Name']))
    actual = {(normalized(d.metadata['Name']), d.version) for d in distributions}
    declared = {(normalized(d['name']), d['version']) for d in runtime_manifest['packages']}
    if actual != declared or len(actual) != len(distributions):
        raise CollectionError('runtime_manifest_distribution_mismatch')
    lock_path = ROOT / 'uv.lock'
    locked = {}
    for package in tomllib.loads(lock_path.read_text())['package']:
        locked.setdefault((normalized(package['name']), package['version']), []).append(package)
    previous_report = {}
    if destination.exists() and any(destination.iterdir()):
        receipt = destination / 'source-manifest.json'
        if not receipt.is_file():
            raise CollectionError('destination_not_an_owned_source_pack')
        previous_report = json.loads(receipt.read_text())
        if previous_report.get('schema_version') != 1:
            raise CollectionError('source_pack_receipt_invalid')
    destination.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(manifest_path, destination / 'runtime-manifest.json')
    shutil.copyfile(lock_path, destination / 'uv.lock')
    active_upstream = [entry for entry in UPSTREAM_SOURCES if entry['name'] != 'sparkle']
    sparkle_info = resources.parent / 'Frameworks/Sparkle.framework/Resources/Info.plist'
    if sparkle_info.is_file():
        sparkle_version = plistlib.loads(sparkle_info.read_bytes()).get('CFBundleShortVersionString')
        if sparkle_version != '2.10.1':
            raise CollectionError('unqualified_sparkle_source_version')
        active_upstream.append(next(entry for entry in UPSTREAM_SOURCES if entry['name'] == 'sparkle'))
    with ThreadPoolExecutor(max_workers=2) as executor:
        packages = list(executor.map(lambda distribution: collect_package(distribution, locked, site, destination, cache, offline), distributions))
        upstream = list(executor.map(lambda entry: collect_upstream(entry, destination, cache, offline, resources), active_upstream))
    imported_native = import_native_evidence(native_evidence, destination, upstream) if native_evidence else None
    # All paths in the portable report are relative to its own source-pack root.
    for record in packages + upstream:
        if record.get('source'):
            record['source']['file'] = str(Path(record['source']['file']).relative_to(destination))
        for key, folder in [('installed_licenses', 'licenses-installed'), ('source_licenses', 'licenses-source')]:
            for license_file in record.get(key, []):
                license_file['file'] = str(Path(record['directory']) / folder / license_file['file'])
    current_directories = {record['directory'] for record in packages + upstream}
    for previous in previous_report.get('packages', []) + previous_report.get('upstream', []):
        old = Path(previous.get('directory', ''))
        if (str(old) not in current_directories and len(old.parts) == 2
                and old.parts[0] in {'packages', 'upstream'} and all(part not in {'.', '..'} for part in old.parts)):
            owned = destination / old
            if owned.is_dir() and not owned.is_symlink():
                shutil.rmtree(owned)
    runtime_license = resources / 'runtime/lib/python3.12/LICENSE.txt'
    if runtime_license.is_file():
        shutil.copyfile(runtime_license, destination / 'CPython-installed-LICENSE.txt')
    report = {'schema_version': 1, 'runtime_manifest_sha256': sha256(manifest_path),
              'uv_lock_sha256': sha256(lock_path), 'package_count': len(packages),
              'packages': packages, 'upstream': upstream, 'native_dependency_review': imported_native,
              'all_python_sdists_retained': all(record.get('source') for record in packages),
              'all_license_texts_retained': all(record.get('installed_licenses') or record.get('source_licenses') for record in packages + upstream),
              'corresponding_source_verified': False,
              'source_gaps': [{'name': record['name'], 'version': record['version'], 'gaps': record['gaps']} for record in packages + upstream if record['gaps']]}
    report['corresponding_source_verified'] = not report['source_gaps']
    (destination / 'source-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    summary = ['TapMesh dependency source pack', '',
               f"Installed Python distributions: {len(packages)}",
               f"Exact lock-hashed sdists retained: {sum(bool(p.get('source')) for p in packages)}",
               f"Upstream source snapshots retained: {sum(bool(p.get('source')) for p in upstream)}/{len(upstream)}",
               f"Corresponding source verified: {str(report['corresponding_source_verified']).lower()}", '',
               'See source-manifest.json for every retained hash, provenance record, license, and remaining gap.',
               'Source archives are stored locally in this pack; opening their URLs is not required.']
    (destination / 'README.txt').write_text('\n'.join(summary) + '\n')
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument('--cache', type=Path, default=CACHE)
    parser.add_argument('--native-evidence', type=Path, help='Checksum-inventoried native dependency review manifest and retained source tree')
    parser.add_argument('--offline', action='store_true', help='Require all archives and tag evidence in the verified cache')
    args = parser.parse_args()
    try:
        report = collect(args.resources, args.destination, cache=args.cache, offline=args.offline, native_evidence=args.native_evidence)
    except (CollectionError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'error', 'code': str(exc) if isinstance(exc, CollectionError) else 'source_collection_failed'}))
        return 1
    print(json.dumps({'status': 'complete' if report['corresponding_source_verified'] else 'incomplete',
                      'packages': report['package_count'],
                      'sdists_retained': sum(bool(p.get('source')) for p in report['packages']),
                      'gap_records': len(report['source_gaps']),
                      'manifest': str(args.destination / 'source-manifest.json')}))
    return 0 if report['corresponding_source_verified'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
