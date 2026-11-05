"""Compatibility entry point; prefer lansync credentials create."""
import argparse
from pathlib import Path
from lansync.credentials import create_credentials


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',action='append',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    print(f'Created credentials in {create_credentials(args.output_dir,args.host)}')
    print('Client needs ca.pem and token.txt. Keep server-key.pem on the server.')

if __name__=='__main__':
    main()
