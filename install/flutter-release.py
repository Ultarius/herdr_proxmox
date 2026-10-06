"""Select an exact official Flutter Linux release; never silently use latest."""
import json
import re
import sys


def select_release(manifest, version, architecture):
    for release in manifest.get('releases', []):
        if (release.get('version') == version and release.get('channel') == 'stable'
                and release.get('dart_sdk_arch', 'x64') == architecture):
            archive, digest = release.get('archive', ''), release.get('sha256', '')
            if not re.fullmatch(r'stable/linux/flutter_linux_[A-Za-z0-9_.-]+[.]tar[.]xz', archive):
                raise ValueError('Unexpected official archive path.')
            if not re.fullmatch(r'[a-f0-9]{64}', digest):
                raise ValueError('Official release is missing its SHA-256 checksum.')
            return archive, digest
    raise ValueError(f'Flutter {version} stable for {architecture} is not in the official manifest.')


if __name__ == '__main__':
    print(*select_release(json.load(sys.stdin), sys.argv[1], sys.argv[2]), sep='\n')
