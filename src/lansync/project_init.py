"""Create a self-contained project skeleton without overwriting existing data."""
from pathlib import Path
import json


def initialize(directory, mode, url=None):
    if mode not in {'client','server'}:
        raise ValueError('Mode must be client or server')
    if mode == 'client':
        from urllib.parse import urlsplit
        parsed=urlsplit(url or '')
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Client URL must be an HTTPS server origin without credentials')
    directory=Path(directory).expanduser().absolute()
    directory.mkdir(parents=False,exist_ok=False)
    common='root = "./shared"\nstate_dir = "./state"\ntoken_file = "./credentials/token.txt"\n'
    if mode=='client':
        text='command = "sync"\nurl = '+json.dumps(url)+'\n'+common+'ca_file = "./credentials/ca.pem"\nwatch = true\nlocal_events = true\nevent_debounce = 0.5\ninterval = 30\nretry_max = 300\n'
    else:
        text='command = "serve"\n'+common+'host = "0.0.0.0"\nport = 8765\ncert_file = "./credentials/server.pem"\nkey_file = "./credentials/server-key.pem"\nallow_write = true\nexclude = ["*.tmp", ".git"]\n'
    (directory/'shared').mkdir()
    (directory/'credentials').mkdir(mode=0o700)
    config=directory/'project.toml'
    with config.open('x',encoding='utf-8') as stream:
        stream.write(text)
    return config
