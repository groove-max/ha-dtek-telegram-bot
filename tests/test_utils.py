import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "rootfs" / "app"
sys.path.insert(0, str(APP_ROOT))

from utils import format_duration


class FormatDurationTest(unittest.TestCase):
    def test_formats_short_duration_in_minutes(self) -> None:
        self.assertEqual(format_duration(59), "0 хв")
        self.assertEqual(format_duration(5 * 60), "5 хв")

    def test_formats_hours_and_minutes(self) -> None:
        self.assertEqual(format_duration((2 * 3600) + (14 * 60)), "2 год 14 хв")

    def test_formats_days_hours_and_minutes(self) -> None:
        seconds = (11 * 24 * 3600) + (17 * 3600) + (59 * 60)
        self.assertEqual(format_duration(seconds), "11 дн 17 год 59 хв")

    def test_formats_months_days_hours_and_minutes(self) -> None:
        seconds = (41 * 24 * 3600) + (3 * 3600) + (5 * 60)
        self.assertEqual(format_duration(seconds), "1 міс 11 дн 3 год 5 хв")

    def test_omits_zero_middle_units(self) -> None:
        self.assertEqual(format_duration(30 * 24 * 3600), "1 міс")
        self.assertEqual(format_duration((30 * 24 * 3600) + (15 * 60)), "1 міс 15 хв")


if __name__ == "__main__":
    unittest.main()
