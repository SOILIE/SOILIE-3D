"""Private remote runtime receiver. Reads short-lived transport data on stdin."""
import base64
import hashlib
import json
from pathlib import Path
import sys
from urllib.request import urlopen

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


if __name__=='__main__':
    request=json.load(sys.stdin)
    dest=Path('/mnt/soilie/runtime-verified.tar.gz')
    partial=dest.with_suffix('.partial')
    digest=hashlib.sha256()
    cipher=Cipher(algorithms.AES(base64.b64decode(request['key'])),modes.GCM(
        base64.b64decode(request['nonce']),base64.b64decode(request['tag']))).decryptor()
    with urlopen(request['url'],timeout=120) as src, partial.open('xb') as output:
        for part in iter(lambda:src.read(4*1024*1024),b''):
            clear=cipher.update(part)
            output.write(clear);digest.update(clear)
        clear=cipher.finalize()
        output.write(clear);digest.update(clear)
    if digest.hexdigest()!=request['sha256']: raise ValueError('Runtime digest mismatch')
    partial.replace(dest)
    print(digest.hexdigest())
