"""Build wheel/sdist and smoke-test the wheel in a fresh virtual environment."""
import argparse
import hashlib
import json
import platform
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def run(args, **kwargs):
    subprocess.run([str(a) for a in args],check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True,help='New directory for release artifacts')
    args=parser.parse_args()
    output=args.output.expanduser().resolve()
    output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1]
    run([sys.executable,'-m','build','--outdir',output,root])
    wheels=list(output.glob('*.whl'))
    if len(wheels)!=1: raise RuntimeError('Expected one wheel')
    with tempfile.TemporaryDirectory(prefix='lansync-install-') as tmp:
        base=Path(tmp)
        venv.EnvBuilder(with_pip=True).create(base/'env')
        bindir=base/'env'/('Scripts' if os.name=='nt' else 'bin')
        python=bindir/('python.exe' if os.name=='nt' else 'python')
        cli=bindir/('lansync.exe' if os.name=='nt' else 'lansync')
        env=dict(os.environ); env.pop('PYTHONPATH',None)
        run([python,'-m','pip','install',str(wheels[0])+'[setup]'],cwd=base,env=env)
        run([python,'-m','pip','check'],cwd=base,env=env)
        run([cli,'--version'],cwd=base,env=env)
        run([cli,'init',base/'project','--mode','client','--url','https://127.0.0.1:8765'],cwd=base,env=env)
        run([cli,'credentials','create','--host','127.0.0.1','--output-dir',base/'server-credentials'],cwd=base,env=env)
        run([cli,'credentials','export-client','--source-dir',base/'server-credentials','--output-dir',base/'project/credentials'],cwd=base,env=env)
        assert {p.name for p in (base/'project/credentials').iterdir()} == {'ca.pem','token.txt'}
        run([cli,'run',base/'project/project.toml','--check'],cwd=base,env=env)
        run([cli,'scan',base/'project/shared','--state-dir',base/'scan-state'],cwd=base,env=env)
        assert (base/'scan-state/manifest.json').is_file()
    artifacts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir() if p.suffix in {'.whl', '.gz'}}
    report = {'installation_smoke_test': 'passed', 'python': sys.version, 'platform': platform.platform(), 'artifacts_sha256': artifacts}
    (output/'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('Wheel installation smoke test passed:',output)

if __name__=='__main__':
    main()
