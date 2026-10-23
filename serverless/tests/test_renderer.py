from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image


os.environ.setdefault("JOB_TABLE", "test-jobs")

renderer = importlib.import_module("serverless.renderer.handler")


class RendererRedeliveryTests(unittest.TestCase):
    def test_webp_preview_preserves_each_model_gif_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            gif_path = root / "model.gif"
            webp_path = root / "preview.webp"
            frames = [
                Image.new("RGB", (4, 4), color)
                for color in ((255, 0, 0), (0, 255, 0), (0, 0, 255))
            ]
            try:
                frames[0].save(
                    gif_path,
                    save_all=True,
                    append_images=frames[1:],
                    duration=[70, 60, 70],
                    loop=0,
                )
            finally:
                for frame in frames:
                    frame.close()

            renderer._webp_from_model_gif(gif_path, webp_path)
            with Image.open(webp_path) as animation:
                self.assertEqual(3, animation.n_frames)
                self.assertEqual(0, animation.info["loop"])

            # Pillow does not expose decoded WebP frame durations. They are
            # stored as 24-bit little-endian milliseconds in each ANMF chunk.
            encoded = webp_path.read_bytes()
            durations = []
            offset = 12  # RIFF header plus the WEBP signature.
            while offset + 8 <= len(encoded):
                chunk_type = encoded[offset:offset + 4]
                chunk_size = int.from_bytes(encoded[offset + 4:offset + 8], "little")
                payload = offset + 8
                if chunk_type == b"ANMF":
                    durations.append(
                        int.from_bytes(encoded[payload + 12:payload + 15], "little")
                    )
                offset = payload + chunk_size + (chunk_size % 2)
            self.assertEqual([70, 60, 70], durations)

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
