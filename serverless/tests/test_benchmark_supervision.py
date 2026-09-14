import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from serverless.benchmark.supervise import command


@unittest.skipUnless(sys.platform == 'linux','Linux ownership boundary')
class SupervisionTests(unittest.TestCase):
    def test_exec_preserves_output_exit_and_works_outside_project(self):
        scratch = Path(__file__).parents[2]/'.codex/tests'
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            result = subprocess.run(command([sys.executable,'-c','print("worker-output"); raise SystemExit(3)']),
                                    cwd=folder,capture_output=True,text=True)
        self.assertEqual(3,result.returncode)
        self.assertEqual('worker-output\n',result.stdout)

    def test_abrupt_parent_death_cannot_leave_a_running_worker(self):
        source = ('import subprocess,sys,time; from serverless.benchmark.supervise import command; '
                  'child=subprocess.Popen(command([sys.executable,"-c","import time; time.sleep(300)"])); '
                  'print(child.pid,flush=True); time.sleep(300)')
        parent = subprocess.Popen([sys.executable,'-c',source],stdout=subprocess.PIPE,text=True)
        child = None
        def running(pid):
            stat = Path(f'/proc/{pid}/stat')
            try:
                return stat.read_text().split(')')[1].strip().split()[0] != 'Z'
            except (FileNotFoundError, ProcessLookupError):
                # The successful parent-death kill can race the /proc read.
                return False
        try:
            child = int(parent.stdout.readline().strip())
            time.sleep(.3) # Let the exec wrapper establish its kernel ownership.
            self.assertTrue(running(child))
            parent.kill(); parent.wait(timeout=5)
            deadline = time.monotonic()+5
            while running(child) and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertFalse(running(child),'Native worker survived its controller')
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait()
            parent.stdout.close()
            if child and running(child):
                os.kill(child,signal.SIGKILL)
