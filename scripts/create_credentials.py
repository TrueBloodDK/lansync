"""Create credentials for a private LAN demonstration (requires .[dev])."""
import argparse
import os
from pathlib import Path
import secrets

import trustme


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', action='append', required=True, help='Server IP or DNS name; repeatable')
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory outside shared folders')
    args = parser.parse_args()
    ca = trustme.CA()
    cert = ca.issue_cert(*args.host)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    files = {'ca.pem': ca.cert_pem.bytes(), 'server.pem': b''.join(blob.bytes() for blob in cert.cert_chain_pems),
             'server-key.pem': cert.private_key_pem.bytes(),
             'token.txt': (secrets.token_urlsafe(32) + '\n').encode('ascii')}
    for name, data in files.items():
        descriptor = os.open(args.output_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(data)
    print(f'Created credentials in {args.output_dir.resolve()}')
    print('Client needs ca.pem and token.txt. Keep server-key.pem on the server.')


if __name__ == '__main__':
    main()
