"""Build and verify one architecture. Use the corresponding Python executable."""
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from importlib.metadata import distribution, PackageNotFoundError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_paths import VERSION
from diagnostics import pe_arch

arch = pe_arch(sys.executable)
if arch not in ('arm64', 'x64'):
    raise SystemExit('Unsupported Python architecture: ' + arch)
name = 'GlassDash-' + arch
release = ROOT / 'release'
build = ROOT / 'build' / arch
build.mkdir(parents=True, exist_ok=True)
release.mkdir(exist_ok=True)
command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--distpath', str(ROOT/'dist'), '--workpath', str(build),
           str(ROOT/'packaging'/'GlassDash.spec')]
if '--package-only' not in sys.argv:
    with (build/'build.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
bundle = ROOT / 'dist' / name
exe = bundle / (name+'.exe')
assert pe_arch(exe) == arch
for item in ('README.md', '使用说明.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md'):
    shutil.copy2(ROOT/item, bundle/item)
licenses = bundle/'licenses'
licenses.mkdir(exist_ok=True)
shutil.copytree(ROOT/'packaging'/'licenses', licenses, dirs_exist_ok=True)
packages = ('PySide6', 'PySide6_Essentials', 'shiboken6', 'numpy', 'Pillow', 'comtypes',
            'requests', 'urllib3', 'certifi', 'charset_normalizer', 'idna', 'pyinstaller')
versions = {}
for package in packages:
    dist = distribution(package)
    versions[package] = dist.version
    for file in dist.files or []:
        if any(word in str(file).lower() for word in ('license', 'copying', 'copyright')):
            original = Path(dist.locate_file(file))
            if original.is_file():
                target = licenses / package / str(file).replace('..', '_')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, target)
python_license = Path(sys.base_prefix)/'LICENSE.txt'
if python_license.exists():
    shutil.copy2(python_license, licenses/'Python-LICENSE.txt')
(bundle/'build-info.json').write_text(json.dumps(dict(version=VERSION, architecture=arch,
    python=sys.version.split()[0], packages=versions), indent=2), encoding='utf-8')
# Exercise the frozen executable, not the source interpreter. No credentials used.
report = build/'self-test.json'
result = subprocess.run([str(exe), '--self-test', str(report)], cwd=bundle, timeout=90)
if result.returncode or not report.exists():
    raise RuntimeError('Packaged self-test failed: ' + str(report))
checks = json.loads(report.read_text(encoding='utf-8'))
assert checks['ok'] and checks['frozen'] and checks['architecture'] == arch, checks
assert checks['requests_ca_present'] and checks['startup_targets_executable'], checks
shutil.copy2(report, bundle/'self-test.json')
archive = release/f'GlassDash-{VERSION}-windows-{arch}.zip'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for file in sorted(bundle.rglob('*')):
        if file.is_file():
            z.write(file, file.relative_to(bundle.parent))
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n', encoding='utf-8')
print(json.dumps(dict(archive=str(archive), bytes=archive.stat().st_size, sha256=digest,
                     self_test=checks), ensure_ascii=False), flush=True)
