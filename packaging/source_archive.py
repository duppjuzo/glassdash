"""Explicit allowlist: never package live configuration, credentials or screenshots."""
import hashlib
import sys
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_paths import VERSION

FILES = ['glassdash.py', 'glass_pages.py', 'liquid_glass.py', 'quota_dashboard.py',
         'quota_fetchers.py', 'battery_stats.py', 'app_paths.py', 'diagnostics.py', 'oauth_config.py',
         'glass_preview.py', 'requirements.txt', 'requirements-build.txt', 'requirements-lock.txt',
         'README.md', '使用说明.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md', '.gitignore',
         'docs/BUILD.md', 'packaging/GlassDash.spec', 'packaging/build.py',
         'packaging/source_archive.py', 'tests/battery_check.py', 'tests/integration_check.py', 'tests/oauth_check.py']
FILES.extend(str(file.relative_to(ROOT)) for file in (ROOT/'packaging'/'licenses').rglob('*.txt'))
release = ROOT/'release'
release.mkdir(exist_ok=True)
archive = release/f'GlassDash-{VERSION}-source.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
    for name in FILES:
        z.write(ROOT/name, f'GlassDash-{VERSION}-source/{name}')
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n', encoding='utf-8')
print(archive)
