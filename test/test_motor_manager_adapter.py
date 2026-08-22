import importlib
from pathlib import Path
import unittest

from PySide6.QtWidgets import QApplication


class MotorManagerAdapterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_creates_page_without_hardware_then_initializes_on_demand(self):
        try:
            module = importlib.import_module("openflex_gui.motor_manager_adapter")
        except ModuleNotFoundError:
            self.fail("openflex_gui.motor_manager_adapter is missing")

        class FakeController:
            def __init__(self, page):
                self.page = page
                self.shutdown_calls = 0
                self.head_controller = type(
                    "HeadController", (), {"is_connected": False}
                )()
                self.column_controller = type(
                    "ColumnController", (), {"is_connected": lambda self: False}
                )()
                self.chassis_controller = type(
                    "ChassisController", (), {"is_connected": False}
                )()

            def shutdown(self):
                self.shutdown_calls += 1

        manager_dir = Path(__file__).resolve().parents[2] / "openflex_manager"
        adapter = module.MotorManagerAdapter(
            manager_dir,
            controller_factory=FakeController,
        )
        page = adapter.create_page()

        self.assertEqual(type(page).__name__, "MotorManagementPage")
        self.assertEqual(page.left_tabs.count(), 4)
        self.assertIsNone(adapter.controller)
        self.assertIsNone(page.controller)

        controller = adapter.initialize_controller()
        self.assertIs(page.controller, adapter.controller)
        self.assertIs(controller, adapter.controller)
        self.assertIs(adapter.initialize_controller(), controller)
        self.assertFalse(adapter.has_active_connection())

        controller.column_controller.is_connected = lambda: True
        self.assertTrue(adapter.has_active_connection())

        adapter.set_theme("day")
        self.assertEqual(page.current_theme, "light")
        adapter.set_theme("night")
        self.assertEqual(page.current_theme, "dark")

        adapter.shutdown()
        adapter.shutdown()
        self.assertTrue(adapter.is_shutdown)
        self.assertEqual(controller.shutdown_calls, 1)


if __name__ == "__main__":
    unittest.main()
