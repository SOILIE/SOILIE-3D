"""Portable collector menu behavior without source datasets."""
from pathlib import Path
import unittest
from unittest.mock import patch

from modules import menu


class CollectorMenuTests(unittest.TestCase):
    def test_princeton_local_root_is_the_project_root(self):
        with patch('builtins.input', side_effect=['l', '0']):
            options = menu.princetonLoop()
        self.assertEqual(options, [str(Path(menu.__file__).resolve().parents[1]), True, 0, None])

    def test_princeton_web_option_keeps_requested_plot_count(self):
        with patch('builtins.input', side_effect=['w', '2']):
            options = menu.princetonLoop()
        self.assertEqual(options, ['http://sun3d.cs.princeton.edu/', False, 2, None])

    def test_washington_local_root_is_the_project_root(self):
        with patch.object(menu.os, 'listdir', return_value=[]), \
             patch.object(menu, 'listdir', return_value=[]), \
             patch('builtins.input', side_effect=['0']):
            options = menu.washingtonLoop()
        self.assertEqual(options, [str(Path(menu.__file__).resolve().parents[1]), True, 0, None])
