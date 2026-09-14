import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from serverless.benchmark.run_campaign import run_stage, stages


class CampaignTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).parents[2]/'.codex/tests'
        base.mkdir(parents=True, exist_ok=True)
        self.scratch = base

    def test_plan_is_serial_original_model_and_never_deploys(self):
        args = argparse.Namespace(runtime=Path('/runtime'),blender=Path('/blender'),infinigen=Path('/infinigen'),
                                  infinigen_blender=Path('/original-blender'),infinigen_packages=Path('/packages'),output=Path('/output'))
        plan = stages(args)
        self.assertEqual(['support-replays','support-parity','infinigen-preflight','infinigen-preflight-export',
                          'diversity-plan','diversity-placements','diversity-export','support-mesh-v2','support-mesh-v2-parity','living-room',
                          'bedroom-10000','infinigen-40','infinigen-final-export'],[row[0] for row in plan])
        self.assertIn('--support',plan[0][1])
        self.assertEqual('40',plan[0][1][-2])
        self.assertIn('10000',dict(plan)['bedroom-10000'])
        self.assertIn('--request-plan',dict(plan)['diversity-placements'])
        self.assertNotIn('fast_solve',str(plan))
        self.assertNotIn('deploy',str(plan))
        self.assertNotIn('publish_comparison',str(plan))

    def test_completed_stage_not_repeated_and_changed_commands_rejected(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            directory = Path(folder)
            (directory/'test.json').write_text(json.dumps({'status':'complete','command':['original']}))
            with patch('serverless.benchmark.run_campaign.subprocess.Popen') as spawn:
                run_stage('test',['original'],directory)
                spawn.assert_not_called()
                with self.assertRaises(ValueError):
                    run_stage('test',['different'],directory)

    def test_failed_stage_is_recorded_not_ignored(self):
        with tempfile.TemporaryDirectory(dir=self.scratch) as folder:
            directory = Path(folder)
            with patch('serverless.benchmark.run_campaign.subprocess.Popen') as spawn:
                spawn.return_value.wait.return_value = 7
                with self.assertRaises(RuntimeError):
                    run_stage('test',['original'],directory)
            row = json.loads((directory/'test.json').read_text())
            self.assertEqual('failed',row['status'])
            self.assertEqual(7,row['exitCode'])
