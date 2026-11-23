"""Build and smoke-test a portable GUI directory on the target operating system."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tarfile
import zipfile
from importlib.metadata import distributions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    report = {'result': 'failed', 'platform': sys.platform}
    try:
        build_env = dict(os.environ)
        if os.name == 'nt':
            # Unrelated SDKs on PATH can supply incompatible DLLs (notably ICU).
            windows = Path(os.environ['SystemRoot'])
            build_env['PATH'] = os.pathsep.join(map(str, [Path(sys.executable).parent,
                                                        Path(sys.base_prefix), windows / 'System32', windows]))
        runtime_data = []
        # Some Python distributions require adjacent PEM data when constructing SSLContext.
        # Preserve their trust policy in the frozen runtime as well.
        for pem in Path(ssl.__file__).parent.glob('*.pem'):
            runtime_data.extend(['--add-data', str(pem) + os.pathsep + '.'])
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', *runtime_data,
                        '--name', 'LANSync', '--paths', str(root / 'src'),
                        '--collect-submodules', 'watchdog.observers',
                        '--workpath', str(output / 'work'), '--specpath', str(output / 'spec'),
                        '--distpath', str(output / 'dist'), str(root / 'scripts/gui_entry.py')], check=True, env=build_env)
        bundle = output / 'dist/LANSync'
        exe = bundle / ('LANSync.exe' if os.name == 'nt' else 'LANSync')
        env = dict(build_env)
        env.pop('PYTHONPATH', None)
        env['QT_QPA_PLATFORM'] = 'offscreen'
        subprocess.run([str(exe), '--smoke-test', str(output / 'smoke')], cwd=output,
                       env=env, timeout=120, check=True)
        smoke = json.loads((output / 'smoke/verification.json').read_text(encoding='utf-8'))
        if smoke['result'] != 'passed' or not smoke['frozen']:
            raise RuntimeError('Frozen GUI smoke check failed')
        # Preserve repository-relative documentation links in the portable package.
        documentation = bundle / 'documentation'
        documentation.mkdir()
        for source in root.glob('*.md'):
            shutil.copyfile(source, documentation / source.name)
        shutil.copytree(root / 'docs', documentation / 'docs')
        (bundle / 'GUI.md').write_text(
            '# LANSync\n\n'
            '[Первый запуск](documentation/docs/GUI_QUICKSTART.md)\n\n'
            '[Демонстрация](documentation/docs/GUI_DEMO.md)\n\n'
            '[Подробная документация](documentation/docs/GUI.md)\n', encoding='utf-8')
        (bundle / 'START.txt').write_text(
            'LANSync desktop client\nExtract the entire directory before launching LANSync.\n'
            'Keep _internal next to the executable. Python installation is not required.\n'
            'This is a development build; project files belong outside this application directory.\n'
            'Russian quick start: documentation/docs/GUI_QUICKSTART.md\n', encoding='utf-8')
        # Preserve available upstream notices from the build environment.
        notices = bundle / 'third-party-notices'
        for dist in distributions():
            for file in dist.files or []:
                if file.name.lower().startswith(('license', 'copying', 'notice')):
                    source = Path(dist.locate_file(file))
                    if source.is_file():
                        target = notices / dist.metadata['Name'] / str(file).replace('..', '_')
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, target)
        suffix = '.zip' if os.name == 'nt' else '.tar.gz'
        archive = output / f"LANSync-{smoke['version']}-{sys.platform}{suffix}"
        if os.name == 'nt':
            with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
                for file in sorted(bundle.rglob('*')):
                    if file.is_file():
                        z.write(file, file.relative_to(bundle.parent))
        else:
            with tarfile.open(archive, 'w:gz', dereference=False) as tar:
                tar.add(bundle, arcname='LANSync')
        report.update(result='passed', version=smoke['version'], smoke=smoke,
                      archive=archive.name, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                      executable=str(exe), executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    finally:
        (output / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('GUI package verified:', archive)


if __name__ == '__main__':
    main()
