"""The start screen's feedback vocabulary: the sentence the footer shows
for a change, and the error-prevention check behind it.  Pure functions,
no display needed; the Tk side is exercised by ``test_start_screen``.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dhakasim.gui import (SETTING_INFO, ROW_KEYS, PREVIEW_NOTES,  # noqa: E402
                          OptionPanel, preview_note, setting_message,
                          PREVIEW_CHOICES, preview_cell_note,
                          setting_problem, help_sections)


class SettingProblemTest(unittest.TestCase):
    def test_good_values_pass(self):
        for key, value in (("end_time", "300"), ("seed", "-1"),
                           ("max_speed", "60"), ("traffic", "1.5"),
                           ("signal_green", "20"), ("speed", "1"),
                           ("accident", "0.5"), ("ppm", "15")):
            self.assertIsNone(setting_problem(key, value), key)

    def test_bad_values_are_named_in_words(self):
        self.assertIn("End time", setting_problem("end_time", "abc"))
        self.assertIn("1 or more", setting_problem("end_time", "0"))
        self.assertIn("whole number", setting_problem("seed", "1.5"))
        self.assertIn("above zero", setting_problem("max_speed", "0"))
        self.assertIn("above zero", setting_problem("traffic", "-2"))
        self.assertIn("at least 1", setting_problem("signal_green", "0"))

    def test_choice_strips_are_never_a_problem(self):
        self.assertIsNone(setting_problem("render3d", "Off"))
        self.assertIsNone(setting_problem("network", "banani_23"))


class SettingMessageTest(unittest.TestCase):
    def test_names_the_setting_its_unit_and_its_meaning(self):
        text = setting_message("max_speed", "45", "60")
        self.assertTrue(text.startswith("Speed limit → 45 km/h: "))
        self.assertIn("speed limit", text)
        self.assertTrue(text.endswith("Was 60 km/h."))

    def test_back_to_the_default_is_said_so(self):
        self.assertTrue(setting_message("end_time", "300", "300")
                        .endswith("Back to the place's default."))

    def test_the_traffic_level_reads_as_a_multiple(self):
        text = setting_message("traffic", "2", "1")
        self.assertIn("→ x2:", text)
        self.assertTrue(text.endswith("Was x1."))

    def test_every_row_key_has_a_vocabulary_entry(self):
        for keys in ROW_KEYS.values():
            for key in keys:
                self.assertIn(key, SETTING_INFO)


class PreviewNoteTest(unittest.TestCase):
    """The caption under the start screen's preview picture."""

    def test_every_choice_of_a_strip_has_a_picture_and_a_line(self):
        self.assertEqual(PREVIEW_CHOICES["signal"],
                         tuple(OptionPanel.SIGNAL_CHOICES))
        self.assertEqual(PREVIEW_CHOICES["render3d"],
                         tuple(OptionPanel.VIEW_CHOICES))
        for key, choices in PREVIEW_CHOICES.items():
            for choice in choices:
                self.assertTrue(preview_cell_note(key, choice, "20"),
                                (key, choice))
        self.assertIn("20 s", preview_cell_note("signal", "Fixed time", "20"))

    def test_every_setting_has_a_note(self):
        for key in SETTING_INFO:
            self.assertIn(key, PREVIEW_NOTES, key)
            note = PREVIEW_NOTES[key]
            values = list(note) if isinstance(note, dict) else ["1"]
            for value in values:
                self.assertTrue(preview_note(key, value, "Banani Road 23"),
                                (key, value))

    def test_every_choice_of_a_switch_has_its_own_note(self):
        for key, choices in (("geometry", ("Off", "On")),
                             ("pedestrians", ("Off", "On")),
                             ("friction", ("Off", "On")),
                             ("render3d", tuple(OptionPanel.VIEW_CHOICES))):
            for choice in choices:
                self.assertTrue(preview_note(key, choice), (key, choice))

    def test_the_note_names_the_value_and_the_place(self):
        self.assertIn("x2", preview_note("traffic", "2"))
        self.assertIn("Kakrail Corridor",
                      preview_note("network", "kakrail_corridor",
                                   "Kakrail Corridor"))
        self.assertIn("seeded", preview_note("seed", "-1"))


class HelpPageTest(unittest.TestCase):
    """The in-app help (F1) is built from the same table the footer uses,
    so every setting on the form is explained on the page."""

    def test_every_setting_is_on_the_help_page(self):
        text = "\n".join(p for _h, ps in help_sections() for p in ps)
        for key, (label, _unit, meaning) in SETTING_INFO.items():
            if key == "ppm":
                continue                # not on the form since 11 Oct 2026
            self.assertIn(label + ":", text, key)
            self.assertIn(meaning[1:], text, key)

    def test_the_page_names_the_keys_and_the_undo(self):
        text = "\n".join(p for _h, ps in help_sections() for p in ps)
        for phrase in ("Ctrl+Z", "Space", "F1", "Reset to defaults",
                       "statistics/csv/", "--headless"):
            self.assertIn(phrase, text, phrase)
        headings = [h for h, _ps in help_sections()]
        self.assertIn("The settings", headings)
        self.assertIn("The run screen", headings)


if __name__ == "__main__":
    unittest.main()
