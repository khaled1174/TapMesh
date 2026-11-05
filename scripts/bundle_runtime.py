"""Build-time embedding of a relocatable runtime; never run on an end user's Mac."""
from pathlib import Path
import hashlib
import csv
import importlib.metadata
import json
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.12.13'
PYTHON_BUILD = '20260510'
PYTHON_ARCHIVE = 'cpython-3.12.13+20260510-aarch64-apple-darwin-install_only_stripped.tar.gz'
PYTHON_SHA256 = '55bc1a5edbc8ac4da0081f4f5731ed2d1ed10c57cb37a820b2a0dbc7cad742e9'
PYTHON_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260510/' + PYTHON_ARCHIVE.replace('+', '%2B')


def source_files():
    roots=['agent','server','Contracts','tapmesh_runtime','macos','scripts','tests','Fixtures','.github']
    files=[ROOT/name for name in ('.gitignore','LICENSE','NOTICE','README.md','appcast.xml','pyproject.toml','uv.lock','requirements-product.lock','requirements-mac.lock','requirements-test.lock','requirements-server.lock')]
    files += [ROOT/('docs/'+name) for name in ('PRODUCT_SCOPE.md','SOURCE_BUILD.md','DEPENDENCY_SOURCES.md','NATIVE_DEPENDENCY_SOURCE_AUDIT.md','RELEASE_STATUS.md')]
    for name in roots:
        directory=ROOT/name
        if directory.exists():
            files += [p for p in directory.rglob('*') if p.is_file() and not p.is_symlink() and not any(part in {'__pycache__','.build','.pytest_cache','.DS_Store','.git','.venv','dist','.runtime'} for part in p.relative_to(ROOT).parts) and p.suffix not in {'.pyc','.token','.p8','.p12','.mobileprovision'} and p.name not in {'.env','connection.json','credentials.json'}]
    return sorted(set(files))


def write_source_archive(path):
    with tarfile.open(path,'w:gz') as archive:
        for source in source_files(): archive.add(source,arcname='TapMesh/'+str(source.relative_to(ROOT)),recursive=False)


def embed(resources, python_source):
    unpacked = None
    if python_source is None:
        cache = Path.home() / 'Library/Caches/TapMesh/dependency-archives'
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / PYTHON_ARCHIVE
        if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
            temporary = archive.with_suffix('.download')
            with urllib.request.urlopen(PYTHON_URL, timeout=60) as source, temporary.open('wb') as destination:
                shutil.copyfileobj(source, destination)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != PYTHON_SHA256:
                temporary.unlink()
                raise SystemExit('Python dependency archive checksum mismatch.')
            temporary.replace(archive)
        unpacked = tempfile.TemporaryDirectory(prefix='python-source-', dir=cache)
        with tarfile.open(archive) as source:
            source.extractall(unpacked.name, filter='data')
        python_source = Path(unpacked.name) / 'python'
    python_source = python_source.resolve()
    if unpacked is None and (python_source / 'BUILD').read_text().strip() != PYTHON_BUILD:
        raise SystemExit('Use the pinned Python build-standalone 20260510 CPython 3.12.13 runtime.')
    python = python_source / 'bin/python3'
    version = subprocess.check_output([str(python), '-I', '-c', 'import platform; print(platform.python_version())'], text=True).strip()
    if version != PYTHON_VERSION:
        raise SystemExit('Unqualified Python runtime version.')
    runtime = resources / 'runtime'
    shutil.copytree(python_source, runtime, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if unpacked:
        unpacked.cleanup()
    site = runtime / 'lib/python3.12/site-packages'
    # Never copy packages from the developer's interpreter or virtualenv.
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    subprocess.run(['uv', '-q', 'pip', 'sync', '--python', str(runtime / 'bin/python3'), '--target', str(site),
                    '--require-hashes', '--only-binary', ':all:', str(ROOT / 'requirements-product.lock')], check=True)
    for name in ('agent', 'server', 'Contracts', 'tapmesh_runtime'):
        shutil.copytree(ROOT / name, site / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # Entry-point scripts have build-directory shebangs. Services execute modules
    # with their bundled interpreter; no non-relocatable script is distributed.
    for path in (site / 'bin', runtime / 'bin'):
        if path.exists():
            for item in path.iterdir():
                if item.is_file() and not item.is_symlink() and item.read_bytes()[:2] == b'#!':
                    item.unlink()
    for path in runtime.rglob('__pycache__'):
        shutil.rmtree(path)
    for path in site.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.dll', '.exe', '.pdb', '.lib'}:
            path.unlink()  # Unused Windows payloads are not part of the Mac product.
    omitted = []
    for metadata in site.glob('enum_compat-0.0.3.dist-info'):
        distribution = importlib.metadata.PathDistribution(metadata)
        if any(Path(file).parts[0] != metadata.name for file in distribution.files or []):
            raise SystemExit('The enum compatibility package unexpectedly contains runtime files.')
        shutil.rmtree(metadata)
        omitted.append({'name': 'enum-compat', 'version': '0.0.3', 'reason': 'metadata-only Python 2 compatibility distribution; no runtime code on Python 3'})
    for record in site.glob('*.dist-info/RECORD'):
        with record.open(newline='') as source:
            rows = [row for row in csv.reader(source) if row and (site/row[0]).exists()]
        with record.open('w', newline='') as destination:
            csv.writer(destination).writerows(rows)
    for path in runtime.rglob('*'):
        if path.is_symlink() and not path.exists():
            path.unlink()  # Aliases of removed build-only entry-point scripts.
        elif path.is_symlink() and not path.resolve().is_relative_to(runtime.resolve()):
            raise SystemExit('Runtime contains a non-relocatable external symlink.')
    from setup_companion import CACHE, SHA256, VERSION
    archive = CACHE / f'idb-companion-{VERSION}.tar.gz'
    if not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise SystemExit('Run scripts/setup_companion.py to fetch the checksum-pinned companion first.')
    companion = resources / 'idb_companion'
    companion.mkdir()
    with tarfile.open(archive) as source:
        source.extractall(companion, filter='data')
    notices = resources / 'Licenses'
    notices.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'LICENSE', notices / 'TapMesh-GPL-3.0.txt')
    shutil.copyfile(ROOT / 'NOTICE', notices / 'NOTICE.txt')
    source_folder = resources / 'Source'
    source_folder.mkdir()
    write_source_archive(source_folder/'TapMesh-source.tar.gz')
    inventory = []
    for distribution in sorted(importlib.metadata.distributions(path=[str(site)]), key=lambda d: d.metadata['Name'].lower()):
        name = distribution.metadata['Name']
        destination = notices / name
        destination.mkdir()
        metadata = distribution.read_text('METADATA') or ''
        (destination / 'METADATA.txt').write_text(metadata)
        for file in distribution.files or []:
            if any(part.lower() in {'licenses', 'license', 'copying', 'notice'} or part.lower().startswith(('license.', 'copying.', 'notice.')) for part in file.parts):
                origin = distribution.locate_file(file)
                if origin.is_file():
                    filename = '__'.join(file.parts[1:])
                    shutil.copyfile(origin, destination / filename)
        expression = distribution.metadata.get('License-Expression')
        classifiers = [c for c in distribution.metadata.get_all('Classifier', []) if c.startswith('License ::')]
        legacy = distribution.metadata.get('License', '')
        inventory.append({'name': name, 'version': distribution.version, 'license_expression': expression,
                          'license_classifiers': classifiers, 'license_metadata_present': bool(expression or classifiers or legacy)})
    manifest = {'python': {'version': PYTHON_VERSION, 'build': PYTHON_BUILD, 'url': PYTHON_URL, 'archive_sha256': PYTHON_SHA256, 'archive_verified': unpacked is not None},
                'companion': {'version': VERSION, 'archive_sha256': SHA256}, 'packages': inventory,
                'omitted_distributions': omitted,
                'source_evidence': 'The separate release source manifest records corresponding-source verification.'}
    (resources / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def clean_developer_rpaths(path):
    commands=subprocess.check_output(['/usr/bin/otool','-l',str(path)],text=True)
    loading=False; paths=set()
    for line in commands.splitlines():
        line=line.strip()
        if line.startswith('cmd '): loading=line=='cmd LC_RPATH'
        if loading and line.startswith('path '):
            value=line[5:].split(' (offset')[0]
            if value.startswith('/') and not value.startswith(('/usr/lib/','/System/Library/')): paths.add(value)
    for value in sorted(paths):
        result=subprocess.run(['/usr/bin/install_name_tool','-delete_rpath',value,str(path)],capture_output=True)
        if result.returncode: raise SystemExit('Unable to remove a developer-only runtime search path.')


def run_codesign(arguments):
    """Retry only a transient trusted-timestamp failure; never omit timestamping."""
    for attempt in range(3):
        result = subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode == 0:
            return
        detail = result.stderr.lower()
        if attempt == 2 or b'timestamp' not in detail or '--timestamp' not in arguments:
            raise SystemExit('Code signing failed: '+result.stderr.decode('utf-8', 'replace')[:4096])
        time.sleep(1+attempt)


def sign_embedded(resources, identity):
    # Sign each distinct Mach-O from the leaves out. Symlink aliases are not
    # signed twice. Disallow external Homebrew/user dylib references before sign.
    binaries = []
    magic = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}
    for path in resources.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as source:
            if source.read(4) not in magic:
                continue
        clean_developer_rpaths(path)
        links = subprocess.check_output(['/usr/bin/otool', '-l', str(path)], text=True)
        loading = False
        for line in links.splitlines():
            line = line.strip()
            if line.startswith('cmd '):
                loading = line.split()[1] in {'LC_LOAD_DYLIB', 'LC_LOAD_WEAK_DYLIB', 'LC_REEXPORT_DYLIB', 'LC_LOAD_UPWARD_DYLIB'}
            if not loading or not line.startswith('name '):
                continue
            # LC_ID_DYLIB is an install identity, not a dependency to resolve.
            dependency = line[5:].split(' (offset')[0]
            if dependency.startswith('/') and not dependency.startswith(('/usr/lib/', '/System/Library/', '/Library/Developer/PrivateFrameworks/CoreSimulator.framework/')):
                raise SystemExit(f'Non-system absolute dynamic dependency in {path.relative_to(resources)}: {dependency}')
        binaries.append(path)
    command = ['/usr/bin/codesign', '--force', '--sign', identity]
    if identity != '-':
        command += ['--options', 'runtime', '--timestamp']
    companion = resources/'idb_companion/idb_companion'
    containers = [p for p in resources.rglob('*') if p.is_dir() and not p.is_symlink() and p.suffix in {'.app', '.bundle', '.framework', '.xpc'}]
    # The companion executable treats its sibling Info.plist/resources as a
    # bundle. Seal resource bundles and nested apps before sealing that parent.
    ordered = sorted((p for p in binaries if p != companion), key=lambda p: len(p.parts), reverse=True)
    ordered += sorted(containers, key=lambda p: len(p.parts), reverse=True)
    if companion in binaries:
        ordered.append(companion)
    for path in ordered:
        run_codesign(command + [str(path)])
    return len(binaries)
