"""Trusted SDK distribution providers and unprivileged, verified acquisition.

Repository data chooses a requirement, never a URL, extraction directory or
installer command. Provider metadata supplies immutable artifact hashes. Network
access occurs only during explicit preparation, never during analysis reads.
"""
from contextlib import closing
import environment_adapters
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
PROVIDER_VERSION = 1
METADATA_LIMIT = 16 * 1024 * 1024
DOWNLOAD_LIMIT = 1024 * 1024 * 1024
EXTRACT_LIMIT = 4 * DOWNLOAD_LIMIT
FILE_LIMIT = 100_000
HOSTS = frozenset(('dotnetcli.blob.core.windows.net', 'builds.dotnet.microsoft.com',
                  'ci.dot.net', 'download.visualstudio.microsoft.com', 'static.rust-lang.org',
                  'github.com', 'release-assets.githubusercontent.com'))



def trusted_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('SDK provider returned an untrusted download location.')
    return value


class TrustedRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, stream, code, message, headers, url):
        return super().redirect_request(request, stream, code, message, headers, trusted_url(url))


def download(url, destination, limit=DOWNLOAD_LIMIT, cancelled=lambda: False):
    """Stream bounded data; credentials, redirects and cancellation stay scoped."""
    opener = build_opener(TrustedRedirect())
    request = Request(trusted_url(url), headers={'User-Agent': 'Herdr-environments/1'})
    size, deadline = 0, time.monotonic() + 900
    with closing(opener.open(request, timeout=30)) as source, open(destination, 'wb') as output:
        while True:
            if cancelled() or time.monotonic() > deadline:
                raise ValueError('SDK acquisition was cancelled or exceeded its deadline.')
            chunk = source.read(128 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError('SDK provider response exceeds its byte budget.')
            output.write(chunk)


def metadata(url, fetch=download):
    with tempfile.TemporaryDirectory(prefix='herdr-sdk-metadata-') as folder:
        path = Path(folder) / 'metadata'
        fetch(trusted_url(url), path, limit=METADATA_LIMIT)
        return path.read_bytes()


def host_target():
    """Initial acquisition support is explicit: Linux glibc x64/arm64."""
    machine = platform.machine().lower()
    architecture = {'x86_64': 'x64', 'amd64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
    if not sys.platform.startswith('linux') or not architecture or platform.libc_ver()[0] != 'glibc':
        raise ValueError('Managed SDK acquisition currently requires Linux glibc x64 or arm64.')
    return dict(os='linux', architecture=architecture, libc='glibc')


def artifact(url, value, algorithm, **fields):
    if not isinstance(value, str) or not re.fullmatch(
            '[0-9a-fA-F]{' + str(hashlib.new(algorithm).digest_size * 2) + '}', value):
        raise ValueError('SDK distribution metadata has no valid artifact digest.')
    return dict(url=trusted_url(url), hash=value.lower(), algorithm=algorithm, **fields)


def resolve(capability, version, target=None, read=metadata, *, components=()):
    """Resolve an exact SDK through publisher metadata, not a release-wide pin."""
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('Acquisition requires a resolved three-part SDK version.')
    target = host_target() if target is None else target
    if target not in (dict(os='linux', architecture='x64', libc='glibc'),
                       dict(os='linux', architecture='arm64', libc='glibc')):
        raise ValueError('The SDK provider does not support this executor.')
    adapter = environment_adapters.get(capability)
    if adapter.provider != 'archive':
        raise ValueError('This provider requires the full requirement declarations; use resolve_requirement.')
    if adapter.resolver is None:
        raise ValueError(f'No trusted artifact provider exists for {capability}.')
    if components:
        if capability != 'rust-toolchain':
            raise ValueError('This SDK does not support optional archive components.')
        url, archives = adapter.resolver(version, target, read, artifact, components=components)
    else:
        url, archives = adapter.resolver(version, target, read, artifact)
    return dict(provider_version=PROVIDER_VERSION, capability=capability,
                version=version, target=target, metadata=url, archives=archives)


def identity(specification):
    return hashlib.sha256(json.dumps(specification, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify_components(specification, root, run=None):
    """The adapter verifies its optional components before reuse/publication."""
    from environment_providers import invoke
    verifier = environment_adapters.get(specification['capability']).component_verifier
    return invoke(verifier, specification, root, run=run) if verifier else {}


def unpack(archive, destination, limit=EXTRACT_LIMIT):
    """Archives publish only ordinary files/directories inside a new private tree."""
    total = 0
    with tarfile.open(archive, 'r:*') as source:
        for count, member in enumerate(source, 1):
            name = PurePosixPath(member.name)
            if (count > FILE_LIMIT or name.is_absolute() or '..' in name.parts
                    or '\\' in member.name or ':' in member.name
                    or not (member.isfile() or member.isdir())):
                raise ValueError('SDK archive contains an unsupported or unsafe entry.')
            total += member.size
            if total > limit:
                raise ValueError('SDK archive exceeds its extraction budget.')
            path = destination.joinpath(*name.parts)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(source.extractfile(member)) as data, path.open('xb') as output:
                shutil.copyfileobj(data, output, 128 * 1024)
            path.chmod(0o755 if member.mode & 0o111 else 0o644)
    return total


def probe(capability, root, run=subprocess.run):
    adapter = environment_adapters.get(capability)
    executable = root / adapter.executable
    environment = dict(os.environ, **adapter.environment(executable))
    environment['PATH'] = str(executable.parent) + os.pathsep + environment.get('PATH', '')
    result = run([str(executable), *adapter.inventory_arguments], cwd=root, env=environment,
                 capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError('The installed SDK probe failed; inspect native host dependencies. '
                         + (result.stderr or '')[-600:])
    output = result.stdout[:16_000]
    versions = re.findall(adapter.version_pattern, output, re.MULTILINE)
    if not versions:
        raise ValueError('The installed SDK did not report an identifiable version.')
    measured = dict(executable=str(executable), versions=versions, output=output)
    if adapter.companion:
        companion = run([adapter.command(executable, adapter.companion), '--version'],
                        cwd=root, env=environment, capture_output=True, text=True, timeout=30)
        match = re.search(adapter.companion_pattern, companion.stdout or '')
        if companion.returncode or not match:
            raise ValueError('The installed ' + (adapter.companion_label or adapter.companion) + ' probe failed; the SDK is incomplete.')
        measured[adapter.companion + '_version'] = match[1]
    return measured


def acquire(specification, store, fetch=download, verify=None, cancelled=lambda: False):
    """Dispatch a frozen specification through a release-registered strategy."""
    import environment_providers
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise ValueError('Managed SDK acquisition must run as an unprivileged service user.')
    provider = environment_providers.for_specification(specification)
    return environment_providers.invoke(provider.acquire, specification, store, fetch=fetch,
                                        verify=verify, cancelled=cancelled)


def acquire_archives(specification, store, fetch=download, verify=None, cancelled=lambda: False):
    """Stage, hash, unpack and probe; publish atomically only after verification."""
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise ValueError('Managed SDK acquisition must run as an unprivileged service user.')
    verify = verify or probe
    key = identity(specification)
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True, mode=0o700)
    if store.is_symlink():
        raise ValueError('The managed SDK store must not be a symlink.')
    final = store / key
    if final.is_symlink():
        raise ValueError('The managed SDK installation must not be a symlink.')
    receipt_path = final / 'herdr-receipt.json'
    def quarantine_final():
        quarantine = store / ('.unverified-' + key + '-' + uuid.uuid4().hex)
        if final.resolve().parent != store.resolve() or quarantine.resolve().parent != store.resolve():
            raise ValueError('The unverified SDK lies outside the managed store.')
        final.rename(quarantine)

    if final.exists():
        try:
            receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            # Preserve the unverified tree for inspection, then acquire afresh.
            # Never certify existing bytes merely because their directory exists.
            quarantine_final()
        except (OSError, ValueError) as error:
            raise ValueError('The existing SDK receipt could not be read; inspect the managed installation.') from error
        else:
            if not isinstance(receipt, dict) or receipt.get('artifact_id') != key or receipt.get('specification') != specification:
                raise ValueError('The existing SDK has an incompatible receipt.')
            # Published paths can already be bound by active plans. A failed
            # reuse probe refuses this acquisition, never moves the installation.
            measured = verify(specification['capability'], final)
            measured['components'] = verify_components(specification, final)
            if specification['version'] not in measured['versions']:
                raise ValueError('The existing SDK no longer matches its receipt; inspect the published installation.')
            return dict(receipt, probe=measured)
    with tempfile.TemporaryDirectory(prefix='.acquiring-', dir=store) as folder:
        stage = Path(folder)
        prefix = stage / 'sdk'
        prefix.mkdir()
        unpacked_bytes = 0
        for index, entry in enumerate(specification['archives']):
            if cancelled():
                raise ValueError('SDK acquisition was cancelled.')
            archive = stage / f'{index}.tar'
            if entry['algorithm'] not in ('sha256', 'sha512'):
                raise ValueError('SDK archives require SHA-256 or SHA-512 verification.')
            fetch(trusted_url(entry['url']), archive, cancelled=cancelled)
            digest = hashlib.new(entry['algorithm'])
            with archive.open('rb') as source:
                for chunk in iter(lambda: source.read(128 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != entry['hash']:
                raise ValueError('SDK archive failed its publisher digest check.')
            adapter = environment_adapters.get(specification['capability'])
            if adapter.unpacker is None:
                raise ValueError('No trusted archive layout is registered for this SDK.')
            unpacked_bytes += adapter.unpacker(archive, prefix, stage, index, EXTRACT_LIMIT - unpacked_bytes, unpack)
        measured = verify(specification['capability'], prefix)
        measured['components'] = verify_components(specification, prefix)
        if specification['version'] not in measured['versions']:
            raise ValueError('The acquired SDK does not report the requested version.')
        measured = dict(measured, executable=str(final / Path(measured['executable']).relative_to(prefix)))
        receipt = dict(artifact_id=key, specification=specification, probe=measured)
        (prefix / 'herdr-receipt.json').write_text(json.dumps(receipt, sort_keys=True), encoding='utf-8')
        if cancelled():
            raise ValueError('SDK acquisition was cancelled before publication.')
        prefix.rename(final)
    # All probes succeeded before publication. The receipt already rebases the
    # executable to the immutable final path; repeat probes only on later reuse.
    return receipt
