import os
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QMetaMethod, QProcess, QSettings, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QLabel,
    QLineEdit,
    QTextEdit,
    QPushButton,
    QSpinBox,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QToolButton,
    QMessageBox,
)

from openflex_gui.main_window import (
    COMBINED_VR_START_DELAY_MS,
    DEACTIVATE_TIMEOUT_SECONDS,
    FORCE_STOP_TIMEOUT_MS,
    MainWindow,
    _ICON_FILE,
)


class FakeMotorManagerAdapter:
    def __init__(self, manager_dir):
        self.manager_dir = manager_dir
        self.page = QFrame()
        self.page.setObjectName("motorManagementPage")
        self.themes = []
        self.is_shutdown = False
        self.shutdown_calls = 0
        self.initialize_calls = 0
        self.active = False

    def create_page(self):
        return self.page

    def set_theme(self, theme):
        self.themes.append(theme)

    def initialize_controller(self):
        self.initialize_calls += 1

    def has_active_connection(self):
        return self.active

    def shutdown(self):
        self.shutdown_calls += 1
        self.is_shutdown = True


class FakeDeploymentRunner:
    def __init__(self, *args, **kwargs):
        self.output = type("Signal", (), {"connect": lambda self, callback: None})()
        self.state_changed = type("Signal", (), {"connect": lambda self, callback: None})()
        self.finished = type("Signal", (), {"connect": lambda self, callback: None})()
        self.is_running = False
        self.active_task = None
        self.starts = []
        self.cancel_calls = 0
        self.inputs = []

    def start(self, task_id, *, dry_run=False, jobs=1, input_lines=None):
        self.starts.append((task_id, dry_run, list(input_lines or [])))
        self.is_running = True
        self.active_task = task_id

    def cancel(self):
        self.cancel_calls += 1

    def send_input(self, text):
        self.inputs.append(text)


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
        self.motor_adapter = None
        self.motor_adapters = []
        self.deployment_runner = None

        def create_motor_adapter(manager_dir):
            self.motor_adapter = FakeMotorManagerAdapter(manager_dir)
            self.motor_adapters.append(self.motor_adapter)
            return self.motor_adapter

        def create_deployment_runner(builder):
            self.deployment_runner = FakeDeploymentRunner(builder)
            return self.deployment_runner

        with patch.object(MainWindow, "_refresh_can_ui_state", lambda self: None):
            self.window = MainWindow(
                settings=self.settings,
                motor_adapter_factory=create_motor_adapter,
                deployment_runner_factory=create_deployment_runner,
            )

    def tearDown(self):
        self.window.close()
        self.settings_dir.cleanup()

    def test_window_uses_four_page_control_center_navigation(self):
        self.assertEqual(self.window.page_stack.count(), 4)
        self.assertEqual(self.window.page_stack.currentIndex(), 0)
        self.assertEqual(
            [button.text() for button in self.window.nav_buttons],
            ["整机控制", "电机管理", "传感器", "部署中心"],
        )
        self.assertIs(self.window.motor_page, self.motor_adapter.page)
        self.assertIs(self.window.motor_manager_adapter, self.motor_adapter)
        self.assertEqual(self.motor_adapter.initialize_calls, 0)

        self.window._set_page(1)

        self.assertEqual(self.window.page_stack.currentIndex(), 1)
        self.assertEqual(self.motor_adapter.initialize_calls, 1)
        self.assertEqual(self.motor_adapter.themes[-1], "day")

    def test_leaving_motor_page_releases_hardware_and_rebuilds_clean_page(self):
        self.window._set_page(1)
        active_adapter = self.motor_adapter

        self.window._set_page(0)

        self.assertEqual(active_adapter.shutdown_calls, 1)
        self.assertIsNot(self.motor_adapter, active_adapter)
        self.assertEqual(self.motor_adapter.initialize_calls, 0)
        self.assertIs(self.window.motor_page, self.motor_adapter.page)

    def test_active_motor_maintenance_blocks_bringup(self):
        self.motor_adapter.active = True

        self.window._on_start_bringup()

        self.assertIsNone(self.window._proc_bringup)
        self.assertEqual(self.window.dot_bringup._state, "error")
        self.assertIn("电机管理", self.window.log_view.toPlainText())

    def test_bringup_hand_mode_toggle_defaults_to_gripper_and_switches_to_o6(self):
        self.assertFalse(self.window.btn_hand_mode.isChecked())
        self.assertEqual(self.window.btn_hand_mode.text(), "夹爪")
        self.assertEqual(
            self.window.btn_hand_mode.toolTip(),
            "切换为灵巧手模式",
        )
        self.assertFalse(self.window._o6_mode)
        self.assertIs(
            self.window.btn_hand_mode.parent(),
            self.window.findChild(QFrame, "panelHeader"),
        )

        self.window.btn_hand_mode.click()

        self.assertTrue(self.window.btn_hand_mode.isChecked())
        self.assertEqual(self.window.btn_hand_mode.text(), "灵巧手")
        self.assertEqual(
            self.window.btn_hand_mode.toolTip(),
            "切换为夹爪模式",
        )
        self.assertTrue(self.window._o6_mode)

    def test_bringup_mode_only_changes_launch_file_and_keeps_original_parameters(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

        self.window._iface_exists = Mock(return_value=True)
        self.window._iface_up = Mock(return_value=True)
        self.window._start_battery_monitor = Mock()
        self.window._launch_process = Mock(return_value=RunningProcess())

        self.window._on_start_bringup()
        gripper_command = self.window._launch_process.call_args.args[0]

        self.window._proc_bringup = None
        self.window._on_proc_finished(
            0, None, self.window.dot_bringup,
            self.window.btn_bringup_start, self.window.btn_bringup_stop,
            "整机控制",
        )
        self.window.btn_hand_mode.click()
        self.window._on_start_bringup()
        o6_command = self.window._launch_process.call_args.args[0]
        self.window._proc_bringup = None

        common_parameters = (
            "use_fake_hardware:=false",
            "chassis_steering_can:=can5",
            "chassis_driving_can:=can4",
            "left_arm_can:=can1",
            "right_arm_can:=can0",
            "lift_can:=can3",
            "lift_node_id:=16",
            "head_can:=can2",
            "use_rviz:=true",
        )
        self.assertIn("integrated_robot_bringup.launch.py", gripper_command)
        self.assertIn("integrated_robot_o6_bringup.launch.py", o6_command)
        for parameter in common_parameters:
            self.assertIn(parameter, gripper_command)
            self.assertIn(parameter, o6_command)

    def test_active_motor_maintenance_blocks_vr(self):
        self.motor_adapter.active = True

        self.window._on_start_vr()

        self.assertIsNone(self.window._proc_vr)
        self.assertEqual(self.window.dot_vr._state, "error")
        self.assertIn("电机管理", self.window.log_view.toPlainText())

    def test_running_bringup_keeps_motor_controller_uninitialized(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._proc_bringup = RunningProcess()

        self.window._set_page(1)

        self.assertEqual(self.motor_adapter.initialize_calls, 0)
        self.assertFalse(self.window.motor_page.isEnabled())
        self.assertIn("暂不可用", self.window.log_view.toPlainText())
        self.window._proc_bringup = None

    def test_window_close_shuts_down_motor_manager_once(self):
        self.window.close()
        self.app.processEvents()

        self.assertEqual(self.motor_adapter.shutdown_calls, 1)

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

    def test_control_page_gives_runtime_log_more_default_width(self):
        splitter = self.window.findChild(QSplitter)

        self.assertIsNotNone(splitter)
        self.window.resize(1200, 800)
        self.window.show()
        self.app.processEvents()
        left_size, right_size = splitter.sizes()
        self.assertGreater(right_size, left_size)

    def test_vr_card_is_below_workflow_in_left_column(self):
        left = self.window.findChild(QFrame, "controlLeftColumn")
        workflow = self.window.findChild(QFrame, "controlWorkflow")
        vr = self.window.findChild(QFrame, "vrCard")
        self.assertIsNotNone(left)
        self.assertIs(vr.parentWidget(), left)
        self.assertGreater(left.layout().indexOf(vr), left.layout().indexOf(workflow))
        self.assertEqual(left.layout().stretch(left.layout().indexOf(workflow)), 0)
        self.assertEqual(left.layout().stretch(left.layout().indexOf(vr)), 0)
        self.assertEqual(
            workflow.sizePolicy().verticalPolicy(),
            QSizePolicy.Policy.Preferred,
        )

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

    def test_control_layout_toggle_moves_log_below_and_restores_side_layout(self):
        splitter = self.window.findChild(QSplitter)
        log_card = self.window.findChild(QFrame, "runtimeLogCard")
        self.window.log_view.setPlainText("保留的诊断日志")

        self.assertEqual(splitter.orientation(), Qt.Horizontal)
        self.assertFalse(self.window.btn_control_layout.icon().isNull())
        self.window.btn_control_layout.click()
        self.app.processEvents()

        self.assertEqual(splitter.orientation(), Qt.Vertical)
        self.assertFalse(self.window.btn_control_layout.icon().isNull())
        self.assertIs(self.window.findChild(QFrame, "runtimeLogCard"), log_card)
        self.assertEqual(self.window.log_view.toPlainText(), "保留的诊断日志")
        self.assertIn("左右", self.window.btn_control_layout.toolTip())

        self.window.btn_control_layout.click()
        self.app.processEvents()

        self.assertEqual(splitter.orientation(), Qt.Horizontal)
        self.assertIs(self.window.findChild(QFrame, "runtimeLogCard"), log_card)
        self.assertEqual(self.window.log_view.toPlainText(), "保留的诊断日志")
        self.assertIn("上下", self.window.btn_control_layout.toolTip())

    def test_vertical_control_layout_keeps_log_after_video_card(self):
        splitter = self.window.findChild(QSplitter)
        left_column = self.window.findChild(QFrame, "controlLeftColumn")
        video_card = self.window.findChild(QFrame, "videoTransferCard")
        self.window.resize(1600, 2200)
        self.window.show()
        self.app.processEvents()

        self.window.btn_control_layout.click()
        self.app.processEvents()

        self.assertLessEqual(left_column.height(), video_card.geometry().bottom() + 1)

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
        self.assertIsNotNone(self.window.btn_deployment_cancel)
        self.assertIsNotNone(self.window.deployment_log)
        self.assertIsNone(self.window.findChild(QLineEdit, "deploymentInput"))
        self.assertIsNone(self.window.findChild(QPushButton, "deploymentSendButton"))

    def test_deployment_page_uses_web_task_card_layout(self):
        cards = self.window.findChildren(QFrame, "deploymentTaskCard")
        self.assertEqual(len(cards), 7)
        self.assertEqual(
            [card.property("taskId") for card in cards],
            ["environment", "compile", "kcan", "vr", "camera", "lidar", "sync-source"],
        )
        self.assertEqual(
            [card.findChild(QLabel, "deploymentCardTitle").text() for card in cards][-1],
            "下载与更新",
        )
        self.assertIsNotNone(self.window.findChild(QFrame, "deploymentActions"))
        self.assertIsNone(self.window.findChild(QCheckBox, "deploymentDryRun"))
        self.assertIsNone(self.window.findChild(QLabel, "deploymentSelection"))
        self.assertIsNone(self.window.findChild(QPushButton, "deploymentCheckButton"))
        self.assertIsNone(self.window.findChild(QPushButton, "deploymentPreviewButton"))
        self.assertIsNone(self.window.findChild(QPushButton, "deploymentStartButton"))

    def test_sensor_page_has_dedicated_log_and_sensor_output_is_routed_to_it(self):
        sensor_log = self.window.findChild(QTextEdit, "sensorLog")
        self.assertIsNotNone(sensor_log)
        self.assertIsNotNone(self.window.findChild(QFrame, "sensorLogCard"))

        stream = Mock()
        stream.data.return_value = b"camera node: device ready\n"
        process = Mock()
        process.readAllStandardOutput.return_value = stream
        self.window._on_sensor_proc_output(process)

        self.assertIn("camera node: device ready", sensor_log.toPlainText())
        self.assertNotIn("camera node: device ready", self.window.log_view.toPlainText())

    def test_sidebar_can_collapse_and_expand_without_changing_page(self):
        toggle = self.window.findChild(QToolButton, "sidebarToggle")
        sidebar = self.window.findChild(QFrame, "sideBar")
        self.assertIsNotNone(toggle)
        self.assertIsNotNone(sidebar)
        self.assertTrue(self.window.sidebar_expanded)
        self.window._set_page(2)
        expanded_width = sidebar.width()

        toggle.click()
        self.app.processEvents()
        self.assertFalse(self.window.sidebar_expanded)
        self.assertEqual(self.window.page_stack.currentIndex(), 2)
        self.assertLess(sidebar.width(), expanded_width)
        self.assertTrue(all(button.toolTip() for button in self.window.nav_buttons))
        self.assertTrue(all(button.isHidden() for button in self.window.nav_buttons))
        self.assertTrue(self.window.workspace_label.isHidden())
        self.assertTrue(self.window.side_footer.isHidden())
        self.assertEqual(toggle.text(), "›")

        toggle.click()
        self.app.processEvents()
        self.assertTrue(self.window.sidebar_expanded)
        self.assertEqual(self.window.page_stack.currentIndex(), 2)
        self.assertEqual(sidebar.width(), expanded_width)
        self.assertTrue(all(not button.isHidden() for button in self.window.nav_buttons))
        self.assertFalse(self.window.workspace_label.isHidden())
        self.assertFalse(self.window.side_footer.isHidden())
        self.assertEqual(toggle.text(), "‹")

    def test_sensor_log_expands_with_sensor_page(self):
        self.window.show()
        self.window._set_page(2)
        self.app.processEvents()
        sensor_log = self.window.findChild(QTextEdit, "sensorLog")
        self.assertIsNotNone(sensor_log)
        self.window.resize(1200, 760)
        self.app.processEvents()
        short_height = sensor_log.height()
        self.window.resize(1200, 960)
        self.app.processEvents()
        self.assertGreater(sensor_log.height(), short_height)

    def test_deployment_card_executes_its_own_task(self):
        card = self.window.findChild(QFrame, "deploymentTaskCard")
        button = card.findChild(QPushButton)
        self.assertIsNotNone(button)
        with patch("openflex_gui.main_window.DeploymentConfigDialog") as dialog_type:
            dialog_type.return_value.configuration_ready.connect.side_effect = (
                lambda callback: callback({"input_lines": []})
            )
            button.click()
        self.assertEqual(self.deployment_runner.starts, [("environment", False, [])])

    def test_interactive_deployment_uses_task_specific_configuration(self):
        with patch("openflex_gui.main_window.DeploymentConfigDialog") as dialog_type:
            dialog = dialog_type.return_value
            dialog.configuration_ready.connect.side_effect = (
                lambda callback: callback({"input_lines": ["RW", "", "HEAD", "BASE"]})
            )
            self.window._select_deployment_task("camera")
            self.window._on_deployment_run()

        dialog_type.assert_called_once_with("camera", self.window)
        self.assertEqual(
            self.deployment_runner.starts,
            [("camera", False, ["RW", "", "HEAD", "BASE"])],
        )

    def test_cancelled_task_configuration_does_not_start_runner(self):
        with patch("openflex_gui.main_window.DeploymentConfigDialog") as dialog_type:
            dialog_type.return_value.exec.return_value = 0
            self.window._select_deployment_task("camera")
            self.window._on_deployment_run()

        self.assertEqual(self.deployment_runner.starts, [])

    def test_source_sync_starts_without_interactive_input(self):
        with patch("openflex_gui.main_window.DeploymentConfigDialog") as dialog_type:
            dialog_type.return_value.configuration_ready.connect.side_effect = (
                lambda callback: callback({"input_lines": []})
            )
            self.window._select_deployment_task("sync-source")
            self.window._on_deployment_run()

        self.assertEqual(self.deployment_runner.starts, [("sync-source", False, [])])

    def test_source_sync_failure_is_shown_in_result_dialog(self):
        class ResultDialog:
            def __init__(self):
                self.result = None

            def show_execution_result(self, success, exit_code, detail=""):
                self.result = (success, exit_code, detail)

        dialog = ResultDialog()
        self.window._deployment_dialog = dialog
        self.deployment_runner.output_tail = "ERROR: failed to read remote component metadata"

        self.window._on_deployment_finished("sync-source", 7, False)

        self.assertEqual(dialog.result, (False, 7, "ERROR: failed to read remote component metadata"))

    def test_sudo_prompt_uses_dedicated_password_dialog(self):
        self.deployment_runner.is_running = True
        popup = type("Popup", (), {"show_sudo_prompt": lambda self: setattr(self, "shown", True)})()
        self.window._deployment_dialog = popup
        self.window._on_deployment_output("[sudo] openflex 的密码：")

        self.assertTrue(popup.shown)
        self.assertTrue(self.window._sudo_prompt_shown)

    def test_all_deployment_card_actions_use_primary_style(self):
        cards = self.window.findChildren(QFrame, "deploymentTaskCard")
        buttons = [card.findChild(QPushButton) for card in cards]
        self.assertTrue(all(button.property("variant") == "primary" for button in buttons))

    def test_deployment_has_no_preview_or_thread_controls(self):
        self.assertIsNone(self.window.findChild(QSpinBox, "deploymentJobs"))
        self.assertIsNone(self.window.findChild(QCheckBox, "deploymentDryRun"))
        self.assertEqual(self.deployment_runner.starts, [])

    def test_motor_maintenance_blocks_deployment(self):
        self.motor_adapter.active = True

        self.window._on_deployment_run()

        self.assertEqual(self.deployment_runner.starts, [])
        self.assertIn("电机管理", self.window.deployment_log.toPlainText())

    def test_running_deployment_locks_robot_actions(self):
        self.window._on_deployment_state_changed("running")

        for button in (
            self.window.btn_enable_can,
            self.window.btn_bringup_start,
            self.window.btn_bringup_vr_start,
            self.window.btn_bringup_vr_stop,
            self.window.btn_vr_start,
            self.window.btn_camera_ros,
            self.window.btn_camera,
            self.window.btn_lidar,
        ):
            self.assertFalse(button.isEnabled())
        self.assertFalse(self.window.motor_page.isEnabled())

    def test_window_close_cancels_active_deployment(self):
        self.deployment_runner.is_running = True

        self.window.close()
        self.app.processEvents()

        self.assertEqual(self.deployment_runner.cancel_calls, 1)
        self.deployment_runner.is_running = False

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

    def test_video_sources_are_off_by_default(self):
        self.assertFalse(self.window.btn_video_head.isChecked())
        self.assertFalse(self.window.btn_video_left.isChecked())
        self.assertFalse(self.window.btn_video_right.isChecked())
        self.assertEqual(self.window.btn_video_start.text(), "启动图传")
        self.assertEqual(self.window.btn_video_stop.text(), "停止图传")
        self.assertFalse(self.window.btn_video_stop.isEnabled())

    def test_video_start_requires_at_least_one_source(self):
        self.window._on_start_video()

        self.assertIsNone(self.window._proc_video)
        self.assertEqual(self.window.dot_video._state, "error")
        self.assertIn("至少选择一个图传源", self.window.log_view.toPlainText())

    def test_video_start_uses_independent_source_flags(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window.btn_video_head.setChecked(True)
        self.window._launch_process = Mock(return_value=RunningProcess())
        self.window._on_start_video()

        command = self.window._launch_process.call_args.args[0]
        self.assertIn("enable_head_video:=true", command)
        self.assertIn("enable_left_hand_video:=false", command)
        self.assertIn("enable_right_hand_video:=false", command)
        self.assertIn("enable_hand_video:=false", command)
        self.assertFalse(self.window.btn_video_head.isEnabled())
        self.assertFalse(self.window.btn_video_left.isEnabled())
        self.assertFalse(self.window.btn_video_right.isEnabled())

    def test_video_process_exit_restores_source_buttons(self):
        self.window.btn_video_head.setChecked(True)
        self.window._set_video_source_buttons_enabled(False)
        self.window._proc_video = Mock()

        self.window._on_proc_finished(
            0, None, self.window.dot_video, self.window.btn_video_start,
            self.window.btn_video_stop, '图传'
        )

        self.assertIsNone(self.window._proc_video)
        self.assertTrue(self.window.btn_video_head.isEnabled())
        self.assertTrue(self.window.btn_video_left.isEnabled())
        self.assertTrue(self.window.btn_video_right.isEnabled())
        self.assertEqual(self.window.dot_video._state, "idle")

    def test_combined_bringup_vr_controls_are_available(self):
        self.assertEqual(self.window.btn_bringup_vr_start.text(), "启动整机控制+VR")
        self.assertEqual(self.window.btn_bringup_vr_stop.text(), "停止整机控制+VR")
        self.assertTrue(self.window.btn_bringup_vr_start.isEnabled())
        self.assertFalse(self.window.btn_bringup_vr_stop.isEnabled())

    def test_bringup_actions_keep_original_vertical_layout(self):
        self.window.resize(1024, 760)
        self.window.show()
        self.app.processEvents()

        self.assertEqual(
            self.window.btn_bringup_start.x(), self.window.btn_bringup_stop.x()
        )
        self.assertEqual(
            self.window.btn_bringup_vr_start.x(), self.window.btn_bringup_vr_stop.x()
        )
        self.assertGreater(
            self.window.btn_bringup_stop.y(), self.window.btn_bringup_start.y()
        )
        self.assertGreater(
            self.window.btn_bringup_vr_start.y(), self.window.btn_bringup_stop.y()
        )
        self.assertGreaterEqual(self.window.btn_bringup_start.width(), 154)
        primary_step = next(
            step for step in self.window.findChildren(QFrame, "controlStep")
            if step.property("primaryStep")
        )
        self.assertGreaterEqual(primary_step.height(), 196)

    def test_control_splitter_keeps_log_only_slightly_wider(self):
        splitter = self.window.findChild(QSplitter)
        self.window.resize(1360, 820)
        self.window.show()
        self.app.processEvents()
        left_size, right_size = splitter.sizes()
        self.assertGreater(right_size, left_size)
        self.assertLess(right_size - left_size, 140)

    def test_combined_start_does_not_enable_can_and_schedules_four_second_vr_start(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._iface_exists = Mock(return_value=True)
        self.window._iface_up = Mock(return_value=True)
        self.window._start_battery_monitor = Mock()
        self.window._launch_process = Mock(return_value=RunningProcess())
        with patch.object(self.window, "_start_enable_can") as enable_can, \
                patch("openflex_gui.main_window.QTimer.singleShot") as single_shot:
            self.window._on_start_bringup_vr()

        enable_can.assert_not_called()
        single_shot.assert_called_once_with(
            COMBINED_VR_START_DELAY_MS,
            self.window._start_combined_vr_after_delay,
        )
        self.assertTrue(self.window._auto_start_vr_pending)
        self.assertTrue(self.window.btn_bringup_vr_stop.isEnabled())

    def test_combined_delay_starts_vr_without_controller_state_check(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._proc_bringup = RunningProcess()
        self.window._auto_start_vr_pending = True
        self.window._combined_start_active = True
        self.window._on_start_vr = Mock()
        with patch("openflex_gui.main_window.QTimer.singleShot") as single_shot:
            self.window._start_combined_vr_after_delay()

        self.window._on_start_vr.assert_called_once_with()
        single_shot.assert_not_called()
        self.assertFalse(self.window._auto_start_vr_pending)

    def test_combined_delay_uses_checked_vr_chassis_command(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._proc_bringup = RunningProcess()
        self.window._auto_start_vr_pending = True
        self.window._combined_start_active = True
        self.window._launch_process = Mock(return_value=RunningProcess())
        with patch.object(self.window, "_start_battery_monitor"), \
                patch("openflex_gui.main_window.QTimer.singleShot"):
            self.window._start_combined_vr_after_delay()

        command = self.window._launch_process.call_args.args[0]
        self.assertIn("integrated_vr_teleop.launch.py vr_chassis:=true", command)

    def test_combined_delay_cancels_when_bringup_is_not_running(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._auto_start_vr_pending = True
        self.window._combined_start_active = True
        self.window._combined_bringup_owned = True
        self.window._set_combined_buttons(False, True)
        with patch("openflex_gui.main_window.QTimer.singleShot") as single_shot:
            self.window._start_combined_vr_after_delay()

        single_shot.assert_not_called()
        self.assertFalse(self.window._auto_start_vr_pending)
        self.assertTrue(self.window.btn_bringup_vr_start.isEnabled())
        self.assertFalse(self.window.btn_bringup_vr_stop.isEnabled())

    def test_combined_stop_waits_for_vr_before_deactivating_bringup(self):
        class RunningProcess:
            @staticmethod
            def state():
                return QProcess.Running

            @staticmethod
            def processId():
                return 0

            @staticmethod
            def kill():
                return None

            @staticmethod
            def waitForFinished(_timeout):
                return True

        self.window._proc_bringup = RunningProcess()
        self.window._proc_vr = RunningProcess()
        self.window._combined_start_active = True
        self.window._combined_bringup_owned = True
        self.window._combined_vr_owned = True
        self.window._stop_process = Mock()
        self.window._deactivate_chassis = Mock()

        self.window._on_stop_bringup_vr()

        self.window._stop_process.assert_called_once()
        self.window._deactivate_chassis.assert_not_called()
        self.window._on_proc_finished(0, None, self.window.dot_vr,
                                      self.window.btn_vr_start,
                                      self.window.btn_vr_stop, 'VR 遥操作')
        self.window._deactivate_chassis.assert_called_once_with(then_stop_bringup=True)

    def test_stop_policy_uses_eight_second_force_timeout_and_four_second_deactivation(self):
        self.assertEqual(FORCE_STOP_TIMEOUT_MS, 8000)
        self.assertEqual(DEACTIVATE_TIMEOUT_SECONDS, 4)

    def test_generic_force_stop_kills_the_entire_process_group(self):
        process = Mock()
        process.state.return_value = QProcess.Running
        process.processId.return_value = 1234

        with patch("openflex_gui.main_window.os.getpgid", return_value=4321), \
                patch("openflex_gui.main_window.os.killpg") as killpg:
            self.window._force_kill(process, "测试进程")

        killpg.assert_called_once_with(4321, signal.SIGKILL)
        process.kill.assert_not_called()

    def test_deactivate_completion_dispatches_bringup_stop_on_gui_thread(self):
        class CompletedProcess:
            returncode = 0

            @staticmethod
            def communicate(timeout):
                self.assertEqual(timeout, DEACTIVATE_TIMEOUT_SECONDS)
                return b"", b""

        with patch.object(self.window, "_call_ui") as call_ui:
            self.window._wait_deactivate(CompletedProcess(), then_stop_bringup=True)

        call_ui.assert_called_once_with(self.window._do_stop_bringup)

    def test_deactivate_timeout_also_dispatches_bringup_stop_on_gui_thread(self):
        class TimedOutProcess:
            @staticmethod
            def communicate(timeout):
                raise subprocess.TimeoutExpired("ros2 control", timeout)

            kill = Mock()

        process = TimedOutProcess()
        with patch.object(self.window, "_call_ui") as call_ui:
            self.window._wait_deactivate(process, then_stop_bringup=True)

        process.kill.assert_called_once_with()
        call_ui.assert_called_once_with(self.window._do_stop_bringup)

    def test_window_icon_uses_packaged_png_without_svg_data_uri(self):
        self.assertTrue(_ICON_FILE.endswith(".png"))
        self.assertTrue(os.path.isfile(_ICON_FILE))


if __name__ == "__main__":
    unittest.main()
