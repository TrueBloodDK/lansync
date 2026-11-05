"""Private-LAN credentials and explicit client-only export."""
import os
from pathlib import Path
import secrets
import ssl


def write_bundle(destination, files):
    destination=Path(destination).expanduser().absolute()
    if destination.is_symlink():
        raise ValueError('Credential directory must not be a symlink')
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise ValueError('Credential directory must be new or empty')
    else:
        destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,data in files.items():
        descriptor=os.open(destination/name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data)
    return destination


def create_credentials(destination, hosts):
    if not hosts or any(not isinstance(host,str) or not host.strip() or '://' in host or '/' in host for host in hosts):
        raise ValueError('Specify server IP addresses or DNS names, without scheme or path')
    try:
        import trustme
    except ImportError as exc:
        raise ValueError('Certificate creation requires: python -m pip install "lansync[setup]"') from exc
    ca=trustme.CA()
    certificate=ca.issue_cert(*hosts)
    files={'ca.pem':ca.cert_pem.bytes(),
           'server.pem':b''.join(blob.bytes() for blob in certificate.cert_chain_pems),
           'server-key.pem':certificate.private_key_pem.bytes(),
           'token.txt':(secrets.token_urlsafe(32)+'\n').encode('ascii')}
    return write_bundle(destination,files)


def export_client_credentials(source,destination):
    from .network import read_token
    source=Path(source).expanduser().resolve(strict=True)
    for name in ('ca.pem','token.txt'):
        if (source/name).is_symlink():
            raise ValueError('Credential source files must not be symlinks')
    token=read_token(source/'token.txt')
    ca=(source/'ca.pem').read_bytes()
    ssl.create_default_context(cadata=ca.decode('ascii'))
    return write_bundle(destination,{'ca.pem':ca,'token.txt':(token+'\n').encode('ascii')})
