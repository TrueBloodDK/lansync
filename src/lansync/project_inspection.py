"""Local, read-only project readiness report; never contacts the server."""
import os
from pathlib import Path
import ssl
from .config import load_project


def inspect_project(path):
    checks=[]
    def record(name,ok,detail):
        checks.append({'check':name,'ok':bool(ok),'detail':detail})
    try:
        project=load_project(path)
    except (OSError,ValueError):
        return {'ok':False,'command':None,'checks':[{'check':'configuration','ok':False,'detail':'Cannot load valid configuration; run --check for details'}]}
    args=project.namespace()
    record('configuration',True,'Configuration loaded')
    root=args.root
    record('root',root.is_dir(),'Root must be an existing directory')
    if root.is_dir():
        record('root_readable',os.access(root,os.R_OK|os.X_OK),'Read/search access estimate; not a full scan')
    # The default location is shared with the CLI to avoid diverging identities.
    from .locations import default_state
    state=(getattr(args,'state_dir',None) or default_state(root)).resolve()
    if project.command!='pull':
        record('state_location',state!=root and root not in state.parents,'State must be outside root')
        record('state_type',not state.exists() or state.is_dir(),'State must be a directory or absent')
        ancestor=state
        while not ancestor.exists() and ancestor!=ancestor.parent:
            ancestor=ancestor.parent
        if ancestor.is_dir():
            record('state_parent_access',os.access(ancestor,os.W_OK|os.X_OK),'Write/search access estimate; no probe file created')
        if root.is_dir() and (project.command=='sync' or project.command=='serve' and args.allow_write):
            try: record('same_volume',root.stat().st_dev==ancestor.stat().st_dev,'Recovery requires root and state on the same volume')
            except OSError: record('same_volume',False,'Cannot inspect volumes')
        record('state_lock',not (state/'sync.lock').exists(),'Existing sync.lock requires checking the running process; it is not removed')
        record('pending_replacements',not any((state/'recovery').glob('*.pending.json')),'Pending replacements require manual recovery review')
    for name in ('token_file','ca_file','cert_file','key_file','manifest','log_file'):
        value=getattr(args,name,None)
        if value is not None:
            resolved=value.resolve()
            record(name+'_location',resolved!=root and root not in resolved.parents,'Must be outside synchronized root')
    token=None
    if getattr(args,'token_file',None):
        from .network import read_token
        try:
            token=read_token(args.token_file); record('token',True,'Token format is valid')
        except (OSError,ValueError): record('token',False,'Token is missing, unreadable or invalid')
    if project.command in {'sync','pull'}:
        from .network import make_client
        try:
            with make_client(args.url,token or 'x'*40,args.ca_file,args.allow_http,args.request_timeout): pass
            record('client_tls_configuration',True,'URL and CA loaded; server was not contacted')
        except (OSError,ValueError): record('client_tls_configuration',False,'Invalid URL or unreadable/invalid CA')
    elif project.command=='serve':
        if args.cert_file and args.key_file:
            try:
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(args.cert_file,args.key_file,password=lambda:'')
                record('server_tls_pair',True,'Certificate and key load together; hostname/expiry not checked')
            except (OSError,ValueError): record('server_tls_pair',False,'Certificate/key missing, encrypted or incompatible')
        else:
            record('server_transport',args.allow_http and args.host in {'127.0.0.1','::1'},'TLS required except explicit loopback HTTP')
    log=getattr(args,'log_file',None)
    if log:
        record('log_parent',log.parent.is_dir(),'Log parent must exist')
        record('log_new',not any(Path(str(log)+suffix).exists() or Path(str(log)+suffix).is_symlink() for suffix in ('','.1','.2','.3')),'Log and rotation names must be unused')
        record('log_state_location',log!=state and state not in log.parents,'Log must be outside state')
    return {'ok':all(c['ok'] for c in checks),'command':project.command,'checks':checks}
