"""Linux parent-death ownership for native benchmark workers.

An OS file lock belongs to its Python controller, not to the Blender child.
Without a parent-death signal, a terminal interruption can release the lock
while Blender keeps writing. A fresh exec wrapper avoids preexec_fn's threaded
fork hazards and ensures that losing a controller kills its worker. It never
imports model code, consumes random numbers or changes the command arguments.
"""
import ctypes
import os
from pathlib import Path
import signal
import sys


def command(values):
    if sys.platform != 'linux':
        return list(values)
    # An absolute script works even in a scene directory or when Infinigen's
    # isolated PYTHONPATH deliberately excludes the surrounding project.
    return [sys.executable, str(Path(__file__).resolve()), str(os.getpid()), '--', *map(str,values)]


def main():
    if sys.platform != 'linux' or len(sys.argv) < 4 or sys.argv[2] != '--':
        raise RuntimeError('Expected a Linux parent PID and an executable command')
    expected_parent = int(sys.argv[1])
    libc = ctypes.CDLL(None,use_errno=True)
    # PR_SET_PDEATHSIG survives ordinary exec, so the signal reaches Blender
    # itself. SIGKILL also covers native code that ignores graceful signals.
    if libc.prctl(1,signal.SIGKILL,0,0,0) != 0:
        raise OSError(ctypes.get_errno(),'Unable to establish worker ownership')
    if os.getppid() != expected_parent:
        raise RuntimeError('Controller exited before worker ownership was established')
    os.execvpe(sys.argv[3],sys.argv[3:],os.environ)


if __name__ == '__main__':
    main()
