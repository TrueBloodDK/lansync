"""Black-box HTTPS acceptance scenarios against an installed LANSync executable."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError('Process did not stop gracefully within 15 seconds')


def wait_until(predicate, processes, message, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        check(all(p.poll() is None for p in processes), 'Background process exited: ' + message)
        if predicate():
            return
        check(time.monotonic() < deadline, 'Timed out: ' + message)
        time.sleep(.1)


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
    background = None
    client_log = None
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
        (a/'conflict.txt').write_bytes(b'remote preserved'); (b/'conflict.txt').write_bytes(b'local preserved')
        action('once',expected=2)
        action('resolve','--path','conflict.txt','--choose','both','--copy-path','local-copy.txt')
        check((b/'local-copy.txt').read_bytes()==b'local preserved','Local conflict copy missing')
        check((b/'conflict.txt').read_bytes()==b'remote preserved','Remote conflict version missing')
        action('once')
        check((a/'local-copy.txt').read_bytes()==b'local preserved','Preserved copy not uploaded')
        record('keep both conflict versions and synchronize preserved copy')
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
        # Real processes, persistent state and ordinary SIGTERM; no crash simulation.
        text=project.read_text(encoding='utf-8').replace('interval = 30\n','interval = 1\n').replace('retry_max = 300\n','retry_max = 2\n')
        project.write_text(text+'request_timeout = 2\n',encoding='utf-8')
        client_log=(base/'acceptance-client.log').open('wb')
        def start_client():
            return subprocess.Popen([str(cli),'run',str(project)],cwd=base,env=env,
                                    stdin=subprocess.DEVNULL,stdout=client_log,stderr=subprocess.STDOUT)
        def saved_status():
            path=client/'state/loop-status.json'
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        def has_content(path, content):
            return path.is_file() and path.read_bytes()==content
        background=start_client()
        wait_until(lambda: saved_status().get('status')=='synced',[process,background],'initial background sync')
        (b/'background.txt').write_bytes(b'local event')
        wait_until(lambda: has_content(a/'background.txt',b'local event') and
                   saved_status().get('status')=='synced' and (saved_status().get('report') or {}).get('uploaded',0)>0,
                   [process,background],'background upload')
        (a/'background.txt').write_bytes(b'server polling')
        wait_until(lambda: has_content(b/'background.txt',b'server polling') and
                   saved_status().get('status')=='synced' and (saved_status().get('report') or {}).get('downloaded',0)>0,
                   [process,background],'background download')
        record('continuous upload and download')
        stop_process(process)
        wait_until(lambda: saved_status().get('status')=='retrying',[background],'retry after server shutdown')
        (b/'offline.txt').write_bytes(b'queued while offline')
        process=subprocess.Popen([str(cli),'run',str(config)],cwd=base,env=env,
                                 stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        wait_until(lambda: has_content(a/'offline.txt',b'queued while offline') and saved_status().get('status')=='synced',
                   [process,background],'reconnect and upload offline edit')
        record('server restart and automatic client reconnect')
        stop_process(background)
        check(background.returncode==0,'Client SIGTERM exit code is not zero')
        check(not (client/'state/sync.lock').exists(),'Client lock remains after SIGTERM')
        check(saved_status().get('status')=='stopped','Stopped state not saved')
        (a/'while-stopped.txt').write_bytes(b'persisted baseline')
        background=start_client()
        wait_until(lambda: has_content(b/'while-stopped.txt',b'persisted baseline'),
                   [process,background],'client restart with previous state')
        stop_process(background)
        check(background.returncode==0 and not (client/'state/sync.lock').exists(),'Restarted client did not stop cleanly')
        check(hashes(a)==hashes(b),'Trees differ after restart')
        record('SIGTERM cleanup and client restart with persistent state')
        return scenarios
    finally:
        try:
            stop_process(background)
        finally:
            try:
                stop_process(process)
            finally:
                if client_log is not None:
                    client_log.close()
                log.close()


def main():
    import argparse
    from datetime import datetime, timezone
    import platform
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli',type=Path,required=True,help='Installed Linux lansync executable')
    parser.add_argument('--output',type=Path,required=True,help='New directory for isolated projects, logs and report')
    args=parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('Server acceptance requires Linux')
    cli=args.cli.expanduser().resolve(strict=True)
    base=args.output.expanduser().absolute()
    base.mkdir(parents=True,exist_ok=False,mode=0o700)
    env=dict(os.environ)
    env.pop('PYTHONPATH',None)
    report={'created_utc':datetime.now(timezone.utc).isoformat(),'platform':platform.platform(),
            'cli':str(cli),'result':'failed'}
    try:
        report['scenarios']=run_scenarios(cli,base,env)
        report['result']='passed'
    except Exception as exc:
        report['error']=str(exc)
        raise
    finally:
        (base/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f"Passed {len(report['scenarios'])} scenarios; report: {base / 'verification.json'}")


if __name__=='__main__':
    main()
