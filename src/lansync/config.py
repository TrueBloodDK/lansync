"""Typed project configuration, independent of argparse and terminal I/O."""
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import math
import tomllib
from .options import validate_options

# type, default; None means optional unless listed in REQUIRED.
COMMON = {'root': (Path,None), 'state_dir': (Path,None), 'log_file': (Path,None)}
AUTH = {'token_file': (Path,None), 'ca_file': (Path,None), 'allow_http': (bool,False)}
SCAN = {**COMMON, 'manifest': (Path,None), 'exclude': (list,())}
SCHEMAS = {
 'scan': SCAN,
 'watch': {**SCAN,'debounce':(float,.5),'interval':(float,60),'max_delay':(float,5)},
 'serve': {**COMMON,'token_file':(Path,None),'host':(str,'127.0.0.1'),'port':(int,8765),'cert_file':(Path,None),'key_file':(Path,None),'allow_http':(bool,False),'allow_write':(bool,False),'exclude':(list,())},
 'pull': {**COMMON,**AUTH,'url':(str,None)},
 'sync': {**COMMON,**AUTH,'url':(str,None),'watch':(bool,False),'dry_run':(bool,False),'json':(bool,False),'interval':(float,30),'retry_max':(float,300),'local_events':(bool,False),'event_debounce':(float,.5)},
}
# pull has no persistent client state.
SCHEMAS['pull'] = {k:v for k,v in SCHEMAS['pull'].items() if k!='state_dir'}
REQUIRED = {'scan':('root',),'watch':('root',),'serve':('root','token_file'),
            'pull':('url','root','token_file'),'sync':('url','root','token_file','state_dir')}

@dataclass(frozen=True)
class ProjectConfig:
    command: str
    values: dict

    def namespace(self):
        return SimpleNamespace(command=self.command, **self.values)

    def arguments(self):
        positional = ('url','root') if self.command in {'sync','pull'} else ('root',)
        options=[]
        for key,value in self.values.items():
            if key in positional or value is None or value is False:
                continue
            flag='--'+key.replace('_','-')
            if value is True:
                options.append(flag)
            elif isinstance(value,(list,tuple)):
                options.extend(flag+'='+str(item) for item in value)
            else:
                options.append(flag+'='+str(value))
        return [self.command,*options,'--',*(str(self.values[k]) for k in positional)]


def load_project(path):
    path=Path(path).expanduser().resolve(strict=True)
    with path.open('rb') as stream:
        data=tomllib.load(stream)
    command=data.pop('command',None)
    if not isinstance(command,str) or command not in SCHEMAS:
        raise ValueError('Config command must be scan, watch, serve, pull or sync')
    schema=SCHEMAS[command]
    unknown=data.keys()-schema.keys()
    if unknown:
        raise ValueError('Unknown config keys: '+', '.join(sorted(unknown)))
    for key in REQUIRED[command]:
        if key not in data:
            raise ValueError('Missing config key: '+key)
    values={}
    for key,(kind,default) in schema.items():
        if key not in data:
            values[key]=default
            continue
        value=data[key]
        if kind in (Path,str):
            valid=isinstance(value,str) and bool(value)
        elif kind is bool:
            valid=type(value) is bool
        elif kind is list:
            valid=isinstance(value,list) and all(isinstance(v,str) and v for v in value)
        elif kind is int:
            valid=type(value) is int
        else:
            valid=type(value) in (int,float)
            if valid:
                try: valid=math.isfinite(value)
                except OverflowError: valid=False
        if not valid:
            raise ValueError(f'Invalid type or value for {key}')
        if kind is Path:
            value=Path(value).expanduser()
            if not value.is_absolute(): value=path.parent/value
            value=value.resolve()
        values[key]=value
    project=ProjectConfig(command,values)
    validate_options(project.namespace())
    return project


def load_arguments(path, commands=None):
    """Compatibility adapter; no access to parser internals."""
    return load_project(path).arguments()
