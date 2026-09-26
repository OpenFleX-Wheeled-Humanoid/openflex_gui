import unittest

from PySide6.QtWidgets import QApplication, QLabel

from openflex_gui.deployment_config_dialog import DeploymentConfigDialog
from openflex_gui.camera_detection import parse_realsense_serials


class DeploymentConfigDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_vr_form_returns_device_and_connection_lines(self):
        dialog = DeploymentConfigDialog("vr")

        dialog.vr_device.setCurrentText("Quest")
        dialog.vr_connected.setChecked(True)

        self.assertEqual(
            dialog.configuration(),
            {"input_lines": ["2", "yes"]},
        )

    def test_vr_form_rejects_unconfirmed_connection(self):
        dialog = DeploymentConfigDialog("vr")

        self.assertIsNone(dialog.configuration())
        self.assertIn("连接", dialog.validation_message)

    def test_realsense_output_is_parsed_into_unique_serials(self):
        output = """
        Device Name: Intel RealSense D435I
        Serial Number: 123456789012
        Device Name: Intel RealSense D405
        Serial Number: 987654321098
        Serial Number: 123456789012
        """

        self.assertEqual(
            parse_realsense_serials(output),
            ["123456789012", "987654321098"],
        )

    def test_realsense_table_does_not_treat_header_as_camera_id(self):
        output = """Device Name                   Serial Number       Firmware Version
RealSense D435I               261922074038        5.17.0.10
RealSense D405                260322277356        5.15.1.55
RealSense D405                260322272379        5.15.1.55
"""

        self.assertEqual(
            parse_realsense_serials(output),
            ["261922074038", "260322277356", "260322272379"],
        )

    def test_camera_form_uses_detected_ids_and_preserves_script_order(self):
        dialog = DeploymentConfigDialog(
            "camera", camera_detector=lambda: ["RW123", "HEAD456", "BASE789"]
        )

        dialog.camera_fields["right_wrist"].setCurrentText("RW123")
        dialog.camera_fields["head"].setCurrentText("HEAD456")
        dialog.camera_fields["base"].setCurrentText("BASE789")

        self.assertEqual(
            dialog.configuration(),
            {"input_lines": ["RW123", "", "HEAD456", "BASE789"]},
        )

    def test_camera_refresh_replaces_detected_choices(self):
        detected = [["ONE"], ["TWO", "THREE"]]
        dialog = DeploymentConfigDialog("camera", camera_detector=lambda: detected.pop(0))

        dialog.camera_refresh_button.click()

        self.assertEqual(
            [dialog.camera_fields["head"].itemText(i) for i in range(dialog.camera_fields["head"].count())],
            ["保持当前配置", "TWO", "THREE"],
        )

    def test_lidar_form_returns_enter_confirmation_and_validated_suffixes(self):
        dialog = DeploymentConfigDialog("lidar")

        dialog.host_ip_last.setText("50")
        dialog.lidar_sn_last.setText("73")
        dialog.network_ready.setChecked(True)

        self.assertEqual(
            dialog.configuration(),
            {"input_lines": ["", "50", "73"]},
        )

    def test_lidar_form_rejects_out_of_range_suffix(self):
        dialog = DeploymentConfigDialog("lidar")

        dialog.host_ip_last.setText("256")
        dialog.lidar_sn_last.setText("73")
        dialog.network_ready.setChecked(True)

        self.assertIsNone(dialog.configuration())
        self.assertIn("0 到 255", dialog.validation_message)

    def test_kcan_form_maps_pcan_choice_to_installer_answer(self):
        dialog = DeploymentConfigDialog("kcan")

        dialog.kcan_remove_pcan.setChecked(True)

        self.assertEqual(dialog.configuration(), {"input_lines": ["yes"]})

    def test_source_sync_has_no_interactive_input(self):
        dialog = DeploymentConfigDialog("sync-source")

        self.assertEqual(dialog.configuration(), {"input_lines": []})

    def test_source_sync_dialog_uses_download_update_language(self):
        dialog = DeploymentConfigDialog("sync-source")

        self.assertEqual(dialog.windowTitle(), "下载与更新")
        self.assertIn("逐组件", dialog.subtitle_label.text())
        self.assertIn("备份", dialog.findChild(QLabel, "dialogNotice").text())

    def test_source_sync_failure_result_shows_error_summary(self):
        dialog = DeploymentConfigDialog("sync-source")

        dialog.show_execution_result(False, 7, "ERROR: failed to read remote component metadata")

        self.assertIn("下载与更新失败", dialog.result_message.text())
        self.assertIn("failed to read remote component metadata", dialog.result_detail.text())

    def test_non_interactive_task_returns_empty_input(self):
        dialog = DeploymentConfigDialog("compile")

        self.assertEqual(dialog.configuration(), {"input_lines": []})

    def test_continue_moves_to_confirmation_stage_without_accepting(self):
        dialog = DeploymentConfigDialog("compile")

        dialog.next_button.click()

        self.assertEqual(dialog.stage, "confirm")
        self.assertEqual(dialog.result(), 0)
        self.assertEqual(dialog.next_button.text(), "开始执行")

    def test_start_emits_configuration_and_moves_to_running_stage(self):
        dialog = DeploymentConfigDialog("compile")
        emitted = []
        dialog.configuration_ready.connect(emitted.append)
        dialog.next_button.click()
        dialog.next_button.click()

        self.assertEqual(emitted, [{"input_lines": []}])
        self.assertEqual(dialog.stage, "running")
        self.assertEqual(dialog.next_button.text(), "停止任务")

    def test_result_stage_reports_success(self):
        dialog = DeploymentConfigDialog("compile")

        dialog.show_execution_result(True, 0)

        self.assertEqual(dialog.stage, "result")
        self.assertIn("执行成功", dialog.result_message.text())
        self.assertEqual(dialog.next_button.text(), "关闭")

    def test_switching_stages_removes_nested_configuration_layout(self):
        dialog = DeploymentConfigDialog("vr")
        dialog.vr_connected.setChecked(True)
        dialog.next_button.click()
        dialog.next_button.click()
        dialog.show_execution_result(False, 1)

        self.assertEqual(dialog.stage, "result")
        self.assertIsNone(dialog.vr_device.parentWidget())
        self.assertIsNone(dialog.vr_connected.parentWidget())

    def test_sudo_password_is_emitted_from_popup(self):
        dialog = DeploymentConfigDialog("environment")
        emitted = []
        dialog.sudo_password_submitted.connect(emitted.append)

        dialog.show_sudo_prompt()
        dialog.sudo_password_field.setText("secret")
        dialog.sudo_submit_button.click()

        self.assertEqual(emitted, ["secret"])
        self.assertEqual(dialog.sudo_password_field.text(), "")


if __name__ == "__main__":
    unittest.main()
