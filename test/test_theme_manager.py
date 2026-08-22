import importlib
import os
import tempfile
import unittest

from PySide6.QtCore import QSettings


class ThemeManagerTest(unittest.TestCase):
    def test_defaults_to_day_and_persists_night(self):
        try:
            module = importlib.import_module("openflex_gui.theme_manager")
        except ModuleNotFoundError:
            self.fail("openflex_gui.theme_manager is missing")

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = QSettings(
                os.path.join(temp_dir, "theme.ini"),
                QSettings.Format.IniFormat,
            )
            manager = module.ThemeManager(settings)
            self.assertEqual(manager.current_theme, "day")

            manager.set_theme("night")

            self.assertEqual(module.ThemeManager(settings).current_theme, "night")


if __name__ == "__main__":
    unittest.main()
