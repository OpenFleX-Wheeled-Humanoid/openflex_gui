import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QMetaMethod, QSettings
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea, QSizePolicy

from openflex_gui.main_window import MainWindow, _ICON_FILE


class MainWindowUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings_dir = tempfile.TemporaryDirectory()
        self.settings = QSettings(
            os.path.join(self.settings_dir.name, "openflex-test.ini"),
            QSettings.Format.IniFormat,
        )
        with patch.object(MainWindow, "_refresh_can_ui_state", lambda self: None):
            self.window = MainWindow(settings=self.settings)

    def tearDown(self):
        self.window.close()
        self.settings_dir.cleanup()

    def test_window_uses_three_page_control_center_navigation(self):
        self.assertEqual(self.window.page_stack.count(), 3)
        self.assertEqual(self.window.page_stack.currentIndex(), 0)
        self.assertEqual(
            [button.text() for button in self.window.nav_buttons],
            ["整机控制", "传感器", "部署中心"],
        )

    def test_window_uses_pyside6(self):
        self.assertEqual(QApplication.__module__.split(".")[0], "PySide6")

    def test_theme_toggle_changes_mode_and_tooltip(self):
        self.assertTrue(hasattr(self.window, "theme_manager"))
        self.assertEqual(self.window.theme_manager.current_theme, "day")
        self.window.btn_theme.click()
        self.assertEqual(self.window.theme_manager.current_theme, "night")
        self.assertEqual(self.window.btn_theme.toolTip(), "切换到日间模式")
        self.assertEqual(self.window.btn_theme.text(), "☀")

    def test_control_page_uses_one_ordered_workflow_with_vr_and_log(self):
        workflow = self.window.findChild(QFrame, "controlWorkflow")
        self.assertIsNotNone(workflow)
        self.assertEqual(
            [
                label.text()
                for label in workflow.findChildren(QLabel, "controlStepTitle")
            ],
            ["CAN 总线", "电机状态", "启动整机控制"],
        )
        self.assertIsNotNone(self.window.findChild(QFrame, "vrCard"))
        self.assertEqual(self.window.log_view.objectName(), "runtimeLog")

    def test_vr_card_is_below_workflow_in_left_column(self):
        left = self.window.findChild(QFrame, "controlLeftColumn")
        workflow = self.window.findChild(QFrame, "controlWorkflow")
        vr = self.window.findChild(QFrame, "vrCard")
        self.assertIsNotNone(left)
        self.assertIs(vr.parentWidget(), left)
        self.assertGreater(left.layout().indexOf(vr), left.layout().indexOf(workflow))

    def test_runtime_log_owns_the_full_right_column(self):
        right = self.window.findChild(QFrame, "controlRightColumn")
        log_card = self.window.findChild(QFrame, "runtimeLogCard")
        self.assertIsNotNone(right)
        self.assertIsNotNone(log_card)
        self.assertIs(log_card.parentWidget(), right)
        self.assertEqual(
            log_card.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Expanding,
        )

    def test_night_theme_keeps_runtime_log_title_readable(self):
        self.window.btn_theme.click()
        log_title = self.window.findChild(QLabel, "logTitle")
        self.assertEqual(log_title.text(), "运行诊断")
        self.assertGreaterEqual(log_title.minimumWidth(), 72)

    def test_sensor_and_deploy_pages_keep_existing_scope(self):
        sensor_buttons = [
            self.window.btn_camera_ros,
            self.window.btn_camera,
            self.window.btn_camera_stop,
            self.window.btn_lidar,
            self.window.btn_lidar_stop,
        ]
        self.assertTrue(all(button is not None for button in sensor_buttons))
        self.assertTrue(
            all(
                button.isSignalConnected(QMetaMethod.fromSignal(button.clicked))
                for button in sensor_buttons
            )
        )
        self.assertIsNotNone(self.window.findChild(QFrame, "deployPlaceholder"))

    def test_window_applies_v13_theme_and_responsive_constraints(self):
        self.assertGreaterEqual(self.window.minimumWidth(), 1024)
        self.assertGreaterEqual(self.window.minimumHeight(), 700)
        self.assertIsNotNone(self.window.findChild(QFrame, "topBar"))
        self.assertIsNotNone(self.window.findChild(QFrame, "sideBar"))
        scroll_areas = self.window.findChildren(QScrollArea, "pageScroll")
        self.assertEqual(len(scroll_areas), 3)
        self.assertTrue(all(scroll.widgetResizable() for scroll in scroll_areas))
        self.assertEqual(self.window.log_view.document().maximumBlockCount(), 3000)
        self.assertIn("#eef2f7", self.window.styleSheet())
        self.assertIn("#1e5bd3", self.window.styleSheet())
        self.assertIn('QPushButton[variant="danger"]:disabled', self.window.styleSheet())

    def test_status_text_tracks_status_dot_state(self):
        self.window.dot_can.set_state("running")
        self.assertEqual(self.window.dot_can.status_label.text(), "通道在线")
        self.window.dot_bringup.set_state("error")
        self.assertEqual(self.window.dot_bringup.status_label.text(), "启动异常")

    def test_vr_chassis_control_is_checked_by_default(self):
        self.assertTrue(self.window.chk_vr_chassis.isChecked())

    def test_window_icon_uses_packaged_png_without_svg_data_uri(self):
        self.assertTrue(_ICON_FILE.endswith(".png"))
        self.assertTrue(os.path.isfile(_ICON_FILE))


if __name__ == "__main__":
    unittest.main()
