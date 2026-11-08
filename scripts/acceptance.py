"""Black-box HTTPS acceptance scenarios against an installed LANSync executable."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def run_scenarios(cli, base, env):
    cli=Path(cli).resolve(); base=Path(base).resolve()
    server=base/'acceptance-server'; client=base/'acceptance-client'
    scenarios=[]
    def command(*args, expected=0):
        result=subprocess.run([str(cli),*(str(a) for a in args)],cwd=base,env=env,
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        if result.returncode!=expected:
            raise RuntimeError(f'Command {args[0]}: exit {result.returncode}, expected {expected}: {result.stdout} {result.stderr}')
        return result.stdout
    def record(name):
        scenarios.append({'scenario':name,'result':'passed'})
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    url=f'https://127.0.0.1:{port}'
    command('init',server,'--mode','server')
    command('init',client,'--mode','client','--url',url)
    config=server/'project.toml'
    text=config.read_text(encoding='utf-8').replace('host = "0.0.0.0"','host = "127.0.0.1"').replace('port = 8765',f'port = {port}')
    config.write_text(text,encoding='utf-8')
    command('credentials','create','--host','127.0.0.1','--output-dir',server/'credentials')
    command('credentials','export-client','--source-dir',server/'credentials','--output-dir',client/'credentials')
    check({p.name for p in (client/'credentials').iterdir()}=={'ca.pem','token.txt'},'Private key leaked in bundle')
    record('project initialization and client credential export')
    log=(base/'acceptance-server.log').open('wb')
    process=subprocess.Popen([str(cli),'run',str(config)],cwd=base,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    project=client/'project.toml'
    def action(name,*args,expected=0): return command('run',project,'--action',name,*args,expected=expected)
    try:
        deadline=time.monotonic()+20
        while True:
            if process.poll() is not None: raise RuntimeError('Acceptance server exited before readiness')
            try:
                result=json.loads(action('doctor','--json'))
                check(result['ok'],'Doctor failed');break
            except RuntimeError:
                if time.monotonic()>=deadline: raise
                time.sleep(.2)
        record('verified HTTPS and authorized manifest')
        a=server/'shared'; b=client/'shared'
        (a/'данные').mkdir(); (a/'данные/отчёт.txt').write_text('Привет',encoding='utf-8')
        (a/'empty').mkdir(); (a/'zero').touch(); (a/'conflict.txt').write_bytes(b'base')
        plan=json.loads(action('plan','--json'))
        check(any(e['path']=='conflict.txt' for e in plan['planned']),'Missing plan entry')
        check(not list(b.iterdir()) and not (client/'state').exists(),'Dry run modified destination')
        record('dry-run leaves client files and state untouched')
        action('once')
        check((b/'данные/отчёт.txt').read_text(encoding='utf-8')=='Привет','Unicode transfer failed')
        check((b/'empty').is_dir() and (b/'zero').stat().st_size==0,'Empty objects missing')
        record('download Unicode and empty objects')
        payload=bytes(range(256))*4096
        (b/'binary.bin').write_bytes(payload); action('once')
        check(hashlib.sha256((a/'binary.bin').read_bytes()).digest()==hashlib.sha256(payload).digest(),'Upload hash mismatch')
        (a/'conflict.txt').write_bytes(b'server update'); action('once')
        check((b/'conflict.txt').read_bytes()==b'server update','Update failed')
        record('binary upload and server update')
        (b/'binary.bin').unlink(); action('once'); check(not (a/'binary.bin').exists(),'Server deletion failed')
        (a/'zero').unlink(); action('once'); check(not (b/'zero').exists(),'Client deletion failed')
        record('deletions in both directions')
        (a/'conflict.txt').write_bytes(b'remote choice'); (b/'conflict.txt').write_bytes(b'local choice')
        action('once',expected=2)
        status=json.loads(action('status','--json'))
        check('conflict.txt' in status['last_sync']['conflicts'],'Conflict missing from status')
        action('resolve','--path','conflict.txt','--choose','local')
        check((a/'conflict.txt').read_bytes()==b'local choice','Resolution failed')
        record('conflict status and explicit local resolution')
        for root in (server,client):
            report=json.loads(command('recovery','verify','--state-dir',root/'state','--json'))
            check(report['problems']==0 and report['verified']>0,'Recovery verification failed')
        copies=json.loads(action('recovery-list','--json')); identifier=next(e['id'] for e in copies if e.get('available'))
        restored=base/'restored.txt'
        action('recovery-restore','--id',identifier,'--output',restored)
        original=restored.read_bytes()
        action('recovery-restore','--id',identifier,'--output',restored,expected=1)
        check(restored.read_bytes()==original,'Restore overwrote existing file')
        record('recovery integrity, export and overwrite rejection')
        def hashes(root):
            return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
        check(hashes(a)==hashes(b),'Final trees differ')
        record('final tree hashes match')
        return scenarios
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
        log.close()
