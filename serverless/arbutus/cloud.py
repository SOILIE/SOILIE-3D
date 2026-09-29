"""Own one temporary private benchmark VM; never mutate shared infrastructure.

Application credentials stay on the local machine. The journal identifies the
exact resource we created. An existing unjournalled name is a conflict, not an
invitation to resize, delete or take over someone else's server.
"""
import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import requests


class Cloud:
    def __init__(self, credential):
        self.project = credential['project_id']
        self.http = requests.Session()
        auth = credential['auth_url'].rstrip('/')
        if not auth.startswith('https://'):
            raise ValueError('HTTPS identity endpoint required')
        response = self.http.post(auth + '/auth/tokens', timeout=30, allow_redirects=False,
            json={'auth': {'identity': {'methods': ['application_credential'], 'application_credential': {
                'id': credential['application_credential_id'], 'secret': credential['application_credential_secret']}}}})
        self.check(response, 201)
        token = response.json()['token']
        if token['project']['id'] != self.project:
            raise ValueError('Wrong OpenStack project')
        self.http.headers['X-Auth-Token'] = response.headers['X-Subject-Token']
        self.endpoints = {service['type']: endpoint['url'].rstrip('/') for service in token['catalog']
            for endpoint in service['endpoints'] if endpoint['interface'] == 'public'
            and endpoint.get('region') == credential.get('region_name', 'RegionOne')}

    @staticmethod
    def check(response, expected):
        if response.status_code != expected:
            # Cloud error bodies sometimes repeat sensitive request parameters.
            raise RuntimeError(f'OpenStack HTTP {response.status_code}; expected {expected}')

    def call(self, service, path, payload=None, expected=200):
        url = self.endpoints[service] + path
        if not url.startswith('https://'):
            raise ValueError('HTTPS service endpoint required')
        response = self.http.request('GET' if payload is None else 'POST', url, json=payload,
                                     timeout=60, allow_redirects=False)
        self.check(response, expected)
        return response.json()


def cloud_init(public_key):
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?', public_key.strip()):
        raise ValueError('An Ed25519 public key is required; never provide a private key')
    return '\n'.join([
        '#cloud-config', 'hostname: mike-dev', 'manage_etc_hosts: true',
        'package_update: true', 'packages: [python3-venv, git, curl, pigz, rsync, libgl1, libglu1-mesa, libxi6, libxrender1, libxfixes3, libxkbcommon0, libsm6, libgomp1, libegl1]',
        'ssh_pwauth: false', 'disable_root: true', 'users:',
        '  - name: mikec1', '    groups: [sudo]', '    shell: /bin/bash',
        '    lock_passwd: true', '    sudo: ["ALL=(ALL) NOPASSWD:ALL"]',
        '    ssh_authorized_keys:', '      - ' + json.dumps(public_key.strip()),
        'runcmd:',
        '  - [sh, -c, "printf SOILIE_HOST_KEY=; cat /etc/ssh/ssh_host_ed25519_key.pub"]',
        '  - [sh, -c, "mountpoint -q /mnt && install -d -o mikec1 -g mikec1 /mnt/soilie"]',
        'final_message: "SOILIE benchmark host bootstrap complete"', ''])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--network', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--public-key', type=Path, required=True)
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--console', action='store_true')
    args = parser.parse_args()
    cloud = Cloud(json.loads(args.credentials.read_bytes()))
    servers = cloud.call('compute', '/servers/detail')['servers']
    matches = [s for s in servers if s['name'] == 'mike-dev']
    if args.journal.exists():
        journal = json.loads(args.journal.read_bytes())
        if journal['project'] != cloud.project or len(matches) != 1 or matches[0]['id'] != journal['serverId']:
            raise ValueError('VM journal conflicts with live infrastructure')
        server = matches[0]
        print(json.dumps({k:server.get(k) for k in ('id','name','status','addresses')}))
        if args.console:
            raw = cloud.call('compute', '/servers/'+server['id']+'/action', {'os-getConsoleOutput': {'length': 300}})['output']
            # Only export public host-key evidence, not the rest of cloud-init.
            keys = re.findall(r'SOILIE_HOST_KEY=(ssh-ed25519 [A-Za-z0-9+/=]+)', raw)
            if keys:
                journal['hostKey'] = keys[-1]
                args.journal.write_text(json.dumps(journal, indent=2)+'\n')
                print('Host key verified from authenticated Nova console')
            else:
                print('Host key not yet present in console')
        return
    if matches:
        raise ValueError('Existing mike-dev is not owned by this journal; refusing to adopt it')
    flavor = next(f for f in cloud.call('compute','/flavors/detail')['flavors'] if f['name']=='cb32-120gb-1120')
    groups = cloud.call('network', '/v2.0/security-groups?project_id='+cloud.project)['security_groups']
    group = next(g for g in groups if g['name']=='runner-sg')
    network = cloud.call('network','/v2.0/networks/'+args.network)['network']
    if network['project_id'] != cloud.project:
        raise ValueError('Network belongs to another project')
    image = cloud.call('image','/v2/images/'+args.image)
    if image['status'] != 'active' or 'Ubuntu-24.04' not in image['name']:
        raise ValueError('Expected active Ubuntu 24.04 image')
    quota = cloud.call('compute','/os-quota-sets/'+cloud.project+'/detail')['quota_set']
    # Retain the allocation's existing 10% headroom policy.
    for name, required in [('cores',32), ('ram',122880), ('instances',1)]:
        q=quota[name]
        if q['limit'] >= 0 and q['in_use']+q['reserved']+required > q['limit']*.9:
            raise ValueError('Insufficient quota after reserve: '+name)
    spec = {'name':'mike-dev','flavorRef':flavor['id'],'imageRef':args.image,
            'networks':[{'uuid':args.network}], 'security_groups':[{'name':group['name']}],
            'metadata':{'owner':'mikecichonski','purpose':'soilie-inventory-benchmark','temporary':'true'},
            'user_data':base64.b64encode(cloud_init(args.public_key.read_text()).encode()).decode()}
    print(json.dumps({'name':spec['name'],'flavor':flavor['name'],'vcpus':flavor['vcpus'],
                      'ramMiB':flavor['ram'],'ephemeralGiB':flavor['OS-FLV-EXT-DATA:ephemeral'],
                      'securityGroup':group['name'],'publicIP':False,'create':args.create}))
    if args.create:
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        # Persist intent before the mutation; an interrupted call is inspected,
        # never retried automatically (which could create a second VM).
        intent = args.journal.with_suffix('.intent.json')
        with intent.open('x') as f:
            json.dump({'project':cloud.project,'name':'mike-dev','createdAt':datetime.now(timezone.utc).isoformat()},f)
        created = cloud.call('compute','/servers',{'server':spec},expected=202)['server']
        with args.journal.open('x') as f:
            json.dump({'project':cloud.project,'serverId':created['id'],'name':'mike-dev',
                       'flavor':flavor['name'],'network':args.network,'image':args.image},f,indent=2)
        print('Created temporary VM '+created['id'])


if __name__ == '__main__':
    main()
