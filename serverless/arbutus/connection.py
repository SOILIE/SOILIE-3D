"""Write task-local SSH configuration using Nova-authenticated host-key evidence."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--journal',type=Path,required=True)
    p.add_argument('--address',required=True)
    p.add_argument('--identity',type=Path,required=True)
    args=p.parse_args()
    state=json.loads(args.journal.read_bytes())
    if not args.address.startswith('192.168.214.') or not state.get('hostKey','').startswith('ssh-ed25519 '):
        raise ValueError('Expected private address and console-verified host key')
    root=args.journal.resolve().parent
    alias='soilie-'+state['serverId']
    (root/'known-hosts').write_text(alias+' '+state['hostKey']+'\n')
    (root/'ssh-config').write_text('\n'.join([
        'Include "'+(Path.home()/'.ssh/config').as_posix()+'"',
        'Host soilie-mike-dev', '  HostName '+args.address,'  HostKeyAlias '+alias,
        '  User mikec1', '  IdentityFile "'+args.identity.resolve().as_posix()+'"',
        '  IdentitiesOnly yes', '  ProxyJump candiapl-mmp-jump',
        '  StrictHostKeyChecking yes', '  UserKnownHostsFile "'+(root/'known-hosts').as_posix()+'"',
        '  BatchMode yes', '  ConnectTimeout 15', '  ServerAliveInterval 30',
        '  ServerAliveCountMax 3', '  ForwardAgent no', '']))
    print('Wrote task-local SSH configuration; no global config changed')


if __name__=='__main__': main()
