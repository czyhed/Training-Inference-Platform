"""Download the official ModelScope snapshot and verify every file before publication."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request


def download(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    url = 'https://modelscope.cn/api/v1/models/OpenBMB/VoxCPM2/repo/files?Revision=master&Recursive=true'
    with urllib.request.urlopen(url, timeout=60) as response:
        manifest = json.load(response)
    assert manifest.get('Success'), 'Cannot obtain official model manifest'
    files = [f for f in manifest['Data']['Files'] if f['Type'] == 'blob']

    def one(item: dict) -> None:
        name = item['Path']
        target = (destination / name).resolve()
        assert target.is_relative_to(destination.resolve()), 'Invalid manifest path'
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size == item['Size']:
            with target.open('rb') as existing:
                if hashlib.file_digest(existing, 'sha256').hexdigest() == item['Sha256']:
                    return
        query = urllib.parse.urlencode({'Revision': item['Revision'], 'FilePath': name})
        request = urllib.request.Request('https://modelscope.cn/api/v1/models/OpenBMB/VoxCPM2/repo?' + query)
        partial = target.with_name(target.name + '.partial')
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=120) as response, partial.open('wb') as output:
            while data := response.read(4 * 1024 * 1024):
                digest.update(data)
                output.write(data)
        assert partial.stat().st_size == item['Size'], 'Model file size mismatch: ' + name
        assert digest.hexdigest() == item['Sha256'], 'Model file checksum mismatch: ' + name
        os.replace(partial, target)
        print('VERIFIED', name, item['Size'], flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(one, files))
    (destination / 'verified-manifest.json').write_text(json.dumps(manifest, indent=2))
    print('MODEL_READY', sum(f['Size'] for f in files), flush=True)


if __name__ == '__main__':
    import sys
    download(Path(sys.argv[1]))
