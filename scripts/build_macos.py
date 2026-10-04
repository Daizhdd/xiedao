"""Build, run and archive native macOS bundles, preserving symlinks and permissions."""
import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def command(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--arch', choices=('arm64', 'x86_64'), required=True)
    args = parser.parse_args()
    if sys.platform != 'darwin' or platform.machine() != args.arch:
        parser.error('Run with a native macOS Python matching --arch')
    build = ROOT / 'build'
    build.mkdir(exist_ok=True)
    iconset = build / 'macos.iconset'
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            command('sips', '-z', size * scale, size * scale,
                    ROOT / 'assets/brand-logo.png', '--out', iconset / name,
                    stdout=subprocess.DEVNULL)
    command('iconutil', '-c', 'icns', iconset, '-o', build / 'macos-icon.icns')
    env = os.environ.copy()
    env['XIEDAO_TARGET_ARCH'] = args.arch
    env['PYINSTALLER_VERIFY_BUNDLE_SIGNATURE'] = '1'
    command(sys.executable, '-m', 'PyInstaller', '--clean', '--noconfirm',
            'xiedao-macos.spec', env=env)
    bundle = ROOT / 'dist/写道.app'
    executable = bundle / 'Contents/MacOS/写道'
    command('codesign', '--verify', '--deep', '--strict', '--verbose=2', bundle)
    command('file', executable)
    # The Cocoa platform exercises the actual bundled macOS runtime.
    smoke_env = env.copy()
    smoke_env.pop('QT_QPA_PLATFORM', None)
    report_dir = build / f'verification-{args.arch}'
    command(executable, '--smoke-test', report_dir, env=smoke_env, timeout=120)
    report = json.loads((report_dir / 'smoke.json').read_text(encoding='utf-8'))
    if not report.get('passed') or not report.get('frozen') or report['architecture'] != args.arch:
        raise RuntimeError(f'Bundle verification failed: {report}')
    prohibited = []
    for item in bundle.rglob('*'):
        relative = item.relative_to(bundle)
        public_ca = item.name == 'cacert.pem' and item.parent.name == 'certifi'
        if ((item.suffix.lower() in ('.db', '.sqlite', '.sqlite3', '.key', '.pem')
             and not public_ca)
                or item.name.startswith(('backup_rebuild_', 'rebuild_pending_'))
                or 'data' in relative.parts):
            prohibited.append(str(relative))
    if prohibited:
        raise RuntimeError(f'Private data unexpectedly bundled: {prohibited}')
    output = ROOT / 'release-macos'
    output.mkdir(exist_ok=True)
    staging = build / f'xiedao-macos-{args.arch}'
    staging.mkdir(exist_ok=True)
    # ditto preserves the framework symlinks that a generic ZIP writer can lose.
    command('ditto', bundle, staging / '写道.app')
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / name, staging / name)
    shutil.copytree(ROOT / 'licenses', staging / 'licenses', dirs_exist_ok=True)
    certifi = metadata.distribution('certifi')
    license_file = next(file for file in certifi.files if file.name == 'LICENSE')
    shutil.copy2(certifi.locate_file(license_file),
                 staging / 'licenses/third-party/certifi-LICENSE')
    shutil.copy2(ROOT / 'docs/MACOS.md', staging / 'Mac使用说明.md')
    archive = output / f'xiedao-macos-{args.arch}.zip'
    command('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', staging, archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / f'SHA256SUMS-macos-{args.arch}.txt').write_text(
        f'{digest}  {archive.name}\n', encoding='ascii')
    report.update(archive=archive.name, sha256=digest,
                  source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                        cwd=ROOT, text=True).strip(),
                  signing='ad-hoc; not Apple Developer ID signed or notarized',
                  private_data_audit='passed')
    (output / f'build-info-macos-{args.arch}.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Built and verified {archive.name}: {digest}')


if __name__ == '__main__':
    main()
