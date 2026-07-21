"""Unit tests for the finite, deterministic text-dose parser."""

from __future__ import annotations

import unittest

from safety.text_dose_parser import TextDoseParser


class TextDoseParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = TextDoseParser()

    def test_chinese_with_connector(self):
        # The parser may emit multiple overlapping matches; we assert the
        # primary (high-confidence) one.
        results = self.parser.parse("把氨氯地平加到20毫克每日一次。")
        self.assertTrue(any(r.confidence == "high" for r in results),
                        "expected a high-confidence extraction")
        primary = next(r for r in results if r.confidence == "high")
        self.assertEqual(primary.drug, "氨氯地平")
        self.assertEqual(primary.dose_value, 20.0)
        self.assertEqual(primary.dose_unit, "mg")
        self.assertEqual(primary.frequency_per_day, 1)

    def test_english_with_space(self):
        results = self.parser.parse("amlodipine 20 mg once daily")
        self.assertGreaterEqual(len(results), 1)
        primary = next(r for r in results if r.confidence == "high")
        self.assertEqual(primary.drug, "amlodipine")
        self.assertEqual(primary.dose_value, 20.0)
        self.assertEqual(primary.dose_unit, "mg")
        self.assertEqual(primary.frequency_per_day, 1)
        self.assertEqual(primary.confidence, "high")

    def test_dose_only_low_or_medium_confidence(self):
        # Bare number without drug — confidence must be low or medium.
        results = self.parser.parse("take 5 tablets")
        for r in results:
            self.assertIn(r.confidence, ("low", "medium", "none"))

    def test_no_match(self):
        self.assertEqual(self.parser.parse("完全无关的文字"), [])


if __name__ == "__main__":
    unittest.main()