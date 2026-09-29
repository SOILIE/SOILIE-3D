"""Encrypted temporary S3 transport for a runtime bundle, not result delivery.

The existing data bucket allows public object reads, so plaintext is NEVER
staged there. AES-GCM keys travel only through the pinned SSH channel. The
temporary ciphertext object is deleted after delivery, including on failure.
Research outputs still travel cloud -> local over the authenticated relay.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import boto3
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from serverless.arbutus.worker import sha


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--archive',type=Path,required=True)
    p.add_argument('--ssh-config',type=Path,required=True)
    args=p.parse_args()
    key,nonce=os.urandom(32),os.urandom(12)
    cipher=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor()
    staged=args.archive.with_suffix('.encrypted')
    with args.archive.open('rb') as src, staged.open('xb') as dest:
        for part in iter(lambda:src.read(4*1024*1024),b''): dest.write(cipher.update(part))
        dest.write(cipher.finalize())
    token=str(uuid.uuid4())
    bucket='soilie3d-data'
    object_key='scratch/arbutus-runtime/'+token+'.enc'
    client=boto3.Session(profile_name='darkest',region_name='ca-central-1').client('s3')
    try:
        client.upload_file(str(staged),bucket,object_key,ExtraArgs={'ContentType':'application/octet-stream'})
        print('Encrypted runtime bundle uploaded',flush=True)
        payload={'url':client.generate_presigned_url('get_object',Params={'Bucket':bucket,'Key':object_key},ExpiresIn=1800),
                 'key':base64.b64encode(key).decode(),'nonce':base64.b64encode(nonce).decode(),
                 'tag':base64.b64encode(cipher.tag).decode(),'sha256':sha(args.archive)}
        result=subprocess.run(['ssh','-F',str(args.ssh_config),'soilie-mike-dev',
            'python3 /mnt/soilie/receive_runtime.py'],input=json.dumps(payload),text=True,capture_output=True,timeout=1700)
        # Do not expose network exception bodies, which can contain signed URLs.
        if result.returncode or result.stdout.strip()!=payload['sha256']:
            raise RuntimeError('Encrypted runtime delivery failed; inspect local receipt, not credential-bearing stderr')
        (args.archive.parent/'runtime-delivery.json').write_text(json.dumps({'sha256':payload['sha256'],'verified':True},indent=2))
        print('Runtime delivered with verified SHA-256',flush=True)
    finally:
        client.delete_object(Bucket=bucket,Key=object_key)
        # This owned ciphertext can be rebuilt from the retained local archive.
        staged.unlink()


if __name__=='__main__': main()
