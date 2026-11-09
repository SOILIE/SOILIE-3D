"""The terminal menu must preserve the user's selected mode and count."""
from contextlib import redirect_stdout
import io
import unittest
from unittest.mock import patch

import imagine


class TerminalRequestTests(unittest.TestCase):
    def menu(self, responses):
        with patch("builtins.input", side_effect=responses), redirect_stdout(io.StringIO()):
            return imagine.menuLoop()

    def test_explicit_three_windows_remain_an_exact_list(self):
        self.assertEqual((["window", "window", "window"], 1, True),
                         self.menu(["1", "2", "window window window", "1"]))

    def test_explicit_out_of_range_lists_are_rejected_before_selection(self):
        responses = ["1", "2", "chair table", "chair " * 9, "chair table lamp", "2"]
        self.assertEqual((["chair", "table", "lamp"], 2, True), self.menu(responses))

    def test_lowercase_yes_selects_exact_nonwindow_quota(self):
        with patch.object(imagine.working_combos, "select_exact",
                          return_value=["chair", "table", "lamp", "window"]) as select:
            result = self.menu(["1", "1", "yes", "yes", "3", "1"])
        self.assertEqual((["chair", "table", "lamp", "window"], 1, True), result)
        select.assert_called_once_with(3, allow_duplicates=True)

    def test_uppercase_yes_means_the_same_as_lowercase_yes(self):
        # Both yes/no prompts explicitly accept answers using .lower().
        with patch.object(imagine.working_combos, "select_exact",
                          return_value=["chair", "table", "lamp"]) as select:
            result = self.menu(["1", "1", "YES", "Y", "3", "1"])
        self.assertEqual((["chair", "table", "lamp"], 1, True), result)
        select.assert_called_once_with(3, allow_duplicates=True)

    def test_out_of_menu_generation_number_is_reprompted(self):
        # The generation menu offers only 1..4; 9 must not become an undefined
        # generation mode or consume the next answer as a scene count.
        result = self.menu(["1", "9", "2", "chair table lamp", "1"])
        self.assertEqual((["chair", "table", "lamp"], 1, True), result)
