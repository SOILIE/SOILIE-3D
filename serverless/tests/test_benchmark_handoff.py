import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from serverless.benchmark.handoff import after_stage
from serverless.benchmark.run_batch import ROOT


class HandoffTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix','Linux controller handoff')
    def test_child_finishes_but_old_controller_cannot_start_next_stage(self):
        base = ROOT/'.codex/tests'
        base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            root = Path(folder)
            child_code = "import time; from pathlib import Path; Path('started').touch(); time.sleep(1); Path('completed').touch()"
            code = "import signal,subprocess,sys; from pathlib import Path; signal.signal(signal.SIGTERM,lambda *a:sys.exit(0)); subprocess.run([sys.executable,'-c',sys.argv[2],'serverless.benchmark.fixture'],cwd=sys.argv[3]); Path(sys.argv[3]+'/next-stage').touch()"
            parent = subprocess.Popen([sys.executable,'-c',code,'serverless.benchmark.run_campaign',child_code,str(root)],cwd=ROOT,start_new_session=True)
            try:
                deadline = time.monotonic()+5
                while not (root/'started').exists():
                    if time.monotonic() > deadline:
                        self.fail('Fixture child did not start')
                    time.sleep(.01)
                after_stage(parent.pid,ROOT,root/'handoff.json')
                parent.wait(timeout=5)
                self.assertTrue((root/'completed').exists())
                self.assertFalse((root/'next-stage').exists())
            finally:
                if parent.poll() is None:
                    os.killpg(parent.pid,signal.SIGCONT)
                    os.killpg(parent.pid,signal.SIGKILL)
                    parent.wait()

    def test_refuses_unrelated_process(self):
        with self.assertRaises(ValueError):
            after_stage(os.getpid(),ROOT,ROOT/'.codex/tests/never-created.json')
