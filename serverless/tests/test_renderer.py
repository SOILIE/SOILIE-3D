from __future__ import annotations

import importlib
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


os.environ.setdefault("JOB_TABLE", "test-jobs")

renderer = importlib.import_module("serverless.renderer.handler")


class RendererRedeliveryTests(unittest.TestCase):
    def test_blender_scene_uses_absolute_job_path(self) -> None:
        work_dir = (Path.cwd() / ".codex" / "job-id" / "1").resolve()
        command = renderer._blender_command(work_dir, 123, {"objects": ["bed"]})
        scene_index = command.index("--background") + 1
        self.assertEqual(command[scene_index], str(work_dir / "suggested_setup.blend"))
        self.assertTrue(Path(command[scene_index]).is_absolute())

    def test_blender_diagnostic_tail_is_bounded_and_log_safe(self) -> None:
        diagnostic = renderer._process_output_tail("before\x00" + ("x" * 4000))
        self.assertEqual(len(diagnostic), 3500)
        self.assertNotIn("\x00", diagnostic)

    def test_scene_terminal_check_skips_completed_and_failed_redeliveries(self) -> None:
        client = Mock()
        with patch.object(renderer, "dynamodb", client):
            for status in ("complete", "failed"):
                client.get_item.return_value = {"Item": {"status": {"S": status}}}
                self.assertTrue(renderer._scene_is_terminal("job-id", 0))

            client.get_item.return_value = {"Item": {"status": {"S": "queued"}}}
            self.assertFalse(renderer._scene_is_terminal("job-id", 0))
            client.get_item.assert_called_with(
                TableName="test-jobs",
                Key={"pk": {"S": "job#job-id"}, "sk": {"S": "scene#001"}},
                ProjectionExpression="#status",
                ExpressionAttributeNames={"#status": "status"},
                ConsistentRead=True,
            )
