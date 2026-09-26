from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from .camera_detection import enumerate_realsense_serials


class DeploymentConfigDialog(QDialog):
    """Task-scoped deployment flow: configure, confirm, run, and result."""

    configuration_ready = Signal(dict)
    sudo_password_submitted = Signal(str)
    cancel_execution = Signal()

    def __init__(self, task_id: str, parent=None, camera_detector=None):
        super().__init__(parent)
        self.task_id = task_id
        self.stage = "config"
        self.validation_message = ""
        self._configuration = None
        self.vr_device = self.vr_connected = None
        self.camera_fields = {}
        self.camera_refresh_button = None
        self.camera_detection_status = None
        self._camera_detector = camera_detector or enumerate_realsense_serials
        self.host_ip_last = self.lidar_sn_last = self.network_ready = None
        self.kcan_remove_pcan = None
        self.sudo_password_field = self.sudo_submit_button = None

        self.setObjectName("deploymentConfigDialog")
        self.setModal(True)
        self.setWindowTitle(self._title_for_task())
        self.setMinimumSize(600, 430)
        self.resize(640, 500)
        self._build_shell()
        self._apply_dialog_style()
        self._render_stage("config")

    def _build_shell(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QFrame(); header.setObjectName("dialogHeader")
        header_layout = QHBoxLayout(header); header_layout.setContentsMargins(24, 20, 18, 17)
        title_box = QVBoxLayout(); title_box.setSpacing(3)
        self.title_label = QLabel(self._title_for_task()); self.title_label.setObjectName("dialogTitle")
        self.subtitle_label = QLabel(self._subtitle_for_task()); self.subtitle_label.setObjectName("dialogSubtitle")
        title_box.addWidget(self.title_label); title_box.addWidget(self.subtitle_label)
        header_layout.addLayout(title_box, 1)
        self.close_button = QPushButton("×"); self.close_button.setObjectName("dialogCloseButton"); self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self._close_requested); header_layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addWidget(header)

        self.steps = QHBoxLayout(); self.steps.setContentsMargins(28, 17, 28, 12); self.steps.setSpacing(0)
        for step_id, label in (("config", "配置"), ("confirm", "确认"), ("running", "执行"), ("result", "结果")):
            step = QLabel(label); step.setObjectName(f"dialogStep_{step_id}"); step.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.steps.addWidget(step, 1)
        root.addLayout(self.steps)

        self.body = QFrame(); self.body.setObjectName("dialogBody")
        self.body_layout = QVBoxLayout(self.body); self.body_layout.setContentsMargins(24, 10, 24, 18); self.body_layout.setSpacing(12)
        root.addWidget(self.body, 1)

        footer = QFrame(); footer.setObjectName("dialogFooter")
        footer_layout = QHBoxLayout(footer); footer_layout.setContentsMargins(24, 14, 24, 20)
        self.back_button = QPushButton("取消"); self.back_button.setObjectName("dialogBackButton"); self.back_button.clicked.connect(self._back_requested)
        self.next_button = QPushButton("继续"); self.next_button.setObjectName("dialogNextButton"); self.next_button.setProperty("variant", "primary"); self.next_button.clicked.connect(self._next_requested)
        footer_layout.addWidget(self.back_button); footer_layout.addStretch(1); footer_layout.addWidget(self.next_button)
        root.addWidget(footer)

    def _render_stage(self, stage: str):
        self.stage = stage
        self._clear_layout(self.body_layout)
        for step_id in ("config", "confirm", "running", "result"):
            label = self.findChild(QLabel, f"dialogStep_{step_id}")
            if label is None: continue
            state = "active" if step_id == stage else "done" if self._step_done(step_id) else "idle"
            label.setProperty("state", state); label.style().unpolish(label); label.style().polish(label)
        if stage == "config":
            self._render_config(); self.back_button.setText("取消"); self.back_button.setEnabled(True); self.next_button.setText("继续"); self.next_button.setEnabled(True); self.next_button.setProperty("variant", "primary")
        elif stage == "confirm":
            self._render_confirm(); self.back_button.setText("返回"); self.back_button.setEnabled(True); self.next_button.setText("开始执行"); self.next_button.setEnabled(True); self.next_button.setProperty("variant", "primary")
        elif stage == "running":
            self._render_running(); self.back_button.setText("返回"); self.back_button.setEnabled(False); self.next_button.setText("停止任务"); self.next_button.setEnabled(True); self.next_button.setProperty("variant", "danger")
        else:
            self._render_result(); self.back_button.setText("返回"); self.back_button.setEnabled(True); self.next_button.setText("关闭"); self.next_button.setEnabled(True); self.next_button.setProperty("variant", "primary")
        self.next_button.style().unpolish(self.next_button); self.next_button.style().polish(self.next_button)

    @staticmethod
    def _clear_layout(layout):
        """Remove widgets recursively; form/grid layouts can nest children."""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif child_layout is not None:
                DeploymentConfigDialog._clear_layout(child_layout)

    def _render_config(self):
        if self.task_id == "vr":
            self.vr_device = QComboBox(); self.vr_device.addItems(["Pico", "Quest"]); self.vr_connected = QCheckBox("我已使用 USB 3.0 线连接主机与 VR 设备")
            form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow); form.addRow("VR 设备", self.vr_device); form.addRow("连接状态", self.vr_connected); self.body_layout.addLayout(form); self._add_notice("安装前会自动检查 ADB；如果未安装，会通过系统权限安装。")
        elif self.task_id == "camera":
            self.camera_fields = {}; form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            for key, label in (("right_wrist", "右腕相机 ID"), ("left_wrist", "左腕相机 ID"), ("head", "头部相机 ID"), ("base", "底盘相机 ID")):
                field = QComboBox(); field.addItem("保持当前配置", ""); self.camera_fields[key] = field; form.addRow(label, field)
            self.body_layout.addLayout(form)
            action_row = QHBoxLayout()
            self.camera_refresh_button = QPushButton("刷新相机 ID")
            self.camera_refresh_button.setProperty("variant", "secondary")
            self.camera_refresh_button.clicked.connect(self._refresh_camera_choices)
            self.camera_detection_status = QLabel()
            self.camera_detection_status.setObjectName("dialogCameraStatus")
            action_row.addWidget(self.camera_refresh_button)
            action_row.addWidget(self.camera_detection_status, 1)
            self.body_layout.addLayout(action_row)
            self._refresh_camera_choices()
        elif self.task_id == "lidar":
            self.network_ready = QCheckBox("我已完成主机网口的手动配置"); self.host_ip_last = self._suffix_field(); self.host_ip_last.setPlaceholderText("例如 50"); self.lidar_sn_last = self._suffix_field(); self.lidar_sn_last.setPlaceholderText("例如 73")
            form = QFormLayout(); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow); form.addRow("网口状态", self.network_ready); form.addRow("主机 IP 尾段", self.host_ip_last); form.addRow("雷达 SN 尾段", self.lidar_sn_last); self.body_layout.addLayout(form); self._add_notice("请先在系统网络设置中完成网口配置，再继续执行。", "warning")
        elif self.task_id == "kcan":
            self.kcan_remove_pcan = QCheckBox("卸载现有 PCAN 驱动，避免与 KCAN 冲突"); self.body_layout.addWidget(self.kcan_remove_pcan); self._add_notice("只有确认存在 PCAN 驱动冲突时才选择此项。", "warning")
        else:
            self._add_notice(self._task_note()); self._add_summary("执行模式", "直接执行"); self._add_summary("工作空间", "当前 OpenFlex 工作空间")

    def _render_confirm(self):
        self._add_notice("参数已填写完成，开始执行后会调用原有安装脚本。"); self._add_summary("任务", self._title_for_task()); self._add_summary("工作空间", "当前 OpenFlex 工作空间"); self._add_summary("终端模式", "嵌入式执行"); self._add_summary("影响范围", self._subtitle_for_task()); self._add_notice("确认后将开始修改或安装操作，请确保机器人处于安全状态。", "warning")

    def _render_running(self):
        self._add_notice(f"正在执行 {self._title_for_task()}，日志会持续显示在部署中心。"); progress = QLabel("正在处理任务步骤"); progress.setObjectName("dialogProgress"); self.body_layout.addWidget(progress)
        self.execution_log = QLabel("开始执行任务\n检查工作空间\n运行安装脚本\n等待下一步输出…"); self.execution_log.setObjectName("dialogExecutionLog"); self.execution_log.setWordWrap(True); self.body_layout.addWidget(self.execution_log)

    def _render_result(self):
        success = getattr(self, "_execution_success", False); exit_code = getattr(self, "_exit_code", -1); message = "执行成功" if success else "执行失败"
        if self.task_id == "sync-source":
            message = "下载与更新完成" if success else "下载与更新失败"
        self.result_message = QLabel(f"{message}，退出码为 {exit_code}。"); self.result_message.setObjectName("dialogResultMessage"); self.body_layout.addWidget(self.result_message)
        detail = getattr(self, "_result_detail", "").strip()
        self.result_detail = QLabel(detail if detail else ("任务已完成。" if success else "请查看部署日志获取详细错误信息。")); self.result_detail.setObjectName("dialogResultDetail"); self.result_detail.setWordWrap(True); self.body_layout.addWidget(self.result_detail)
        self._add_summary("任务", self._title_for_task()); self._add_summary("状态", message); self._add_summary("退出码", str(exit_code))

    def _add_notice(self, text: str, kind: str = "info"):
        notice = QLabel(text); notice.setObjectName("dialogNotice"); notice.setProperty("kind", kind); notice.setWordWrap(True); self.body_layout.addWidget(notice)

    def _add_summary(self, label: str, value: str):
        row = QFrame(); row.setObjectName("dialogSummaryRow"); layout = QHBoxLayout(row); layout.setContentsMargins(12, 8, 12, 8); key = QLabel(label); key.setObjectName("dialogSummaryLabel"); val = QLabel(value); val.setObjectName("dialogSummaryValue"); val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); layout.addWidget(key); layout.addWidget(val, 1); self.body_layout.addWidget(row)

    def show_sudo_prompt(self):
        if self.stage != "running": self._render_stage("running")
        self._add_notice("系统权限授权：安装任务请求 sudo 权限。", "warning"); self.sudo_password_field = QLineEdit(); self.sudo_password_field.setEchoMode(QLineEdit.EchoMode.Password); self.sudo_password_field.setPlaceholderText("仅发送到当前任务，不保存"); self.sudo_submit_button = QPushButton("发送授权"); self.sudo_submit_button.setProperty("variant", "primary")
        row = QHBoxLayout(); row.addWidget(self.sudo_password_field, 1); row.addWidget(self.sudo_submit_button); self.body_layout.insertLayout(1, row); self.sudo_submit_button.clicked.connect(self._submit_sudo_password); self.sudo_password_field.returnPressed.connect(self._submit_sudo_password)

    def _submit_sudo_password(self):
        if self.sudo_password_field is None: return
        password = self.sudo_password_field.text()
        if not password: return
        self.sudo_password_submitted.emit(password); self.sudo_password_field.clear(); self.sudo_password_field.setEnabled(False); self.sudo_submit_button.setEnabled(False)

    def update_execution_output(self, text: str):
        if hasattr(self, "execution_log"):
            self.execution_log.setText(text[-4000:])

    def show_execution_result(self, success: bool, exit_code: int, detail: str = ""):
        self._execution_success = success; self._exit_code = exit_code; self._result_detail = detail; self._render_stage("result")

    def _next_requested(self):
        if self.stage == "config":
            configuration = self.configuration()
            if configuration is None: self._add_notice(self.validation_message, "error"); return
            self._configuration = configuration; self._render_stage("confirm")
        elif self.stage == "confirm":
            self.configuration_ready.emit(self._configuration or {"input_lines": []}); self._render_stage("running")
        elif self.stage == "running": self.cancel_execution.emit()
        else: self.accept()

    def _back_requested(self):
        if self.stage == "config": self.reject()
        elif self.stage == "confirm": self._render_stage("config")
        elif self.stage == "result": self.reject()

    def _close_requested(self):
        if self.stage == "running": self.cancel_execution.emit()
        else: self.reject()

    def _step_done(self, step_id: str) -> bool:
        return ("config", "confirm", "running", "result").index(step_id) < ("config", "confirm", "running", "result").index(self.stage)

    def _refresh_camera_choices(self):
        if not self.camera_fields:
            return
        previous = {
            key: str(field.currentData() or "")
            for key, field in self.camera_fields.items()
        }
        try:
            serials = list(dict.fromkeys(self._camera_detector() or []))
        except Exception:
            serials = []
        for key, field in self.camera_fields.items():
            field.blockSignals(True)
            field.clear()
            field.addItem("保持当前配置", "")
            for serial in serials:
                field.addItem(str(serial), str(serial))
            old_value = previous.get(key, "")
            index = field.findData(old_value)
            field.setCurrentIndex(index if index >= 0 else 0)
            field.blockSignals(False)
        if self.camera_detection_status is not None:
            if serials:
                self.camera_detection_status.setText(f"已检测到 {len(serials)} 台 RealSense 相机")
            else:
                self.camera_detection_status.setText("未检测到相机，可刷新或保持当前配置")

    def configuration(self) -> dict | None:
        self.validation_message = ""
        if self.task_id == "vr":
            if not self.vr_connected.isChecked(): self.validation_message = "请先确认头显已通过 USB 3.0 连接。"; return None
            return {"input_lines": ["1" if self.vr_device.currentText() == "Pico" else "2", "yes"]}
        if self.task_id == "camera": return {"input_lines": [str(self.camera_fields[key].currentData() or "").strip() for key in ("right_wrist", "left_wrist", "head", "base")]}
        if self.task_id == "lidar":
            if not self.network_ready.isChecked(): self.validation_message = "请确认已完成主机网口配置。"; return None
            host_last = self._validated_suffix(self.host_ip_last.text(), "主机 IP"); lidar_last = self._validated_suffix(self.lidar_sn_last.text(), "雷达 SN")
            if host_last is None or lidar_last is None: return None
            return {"input_lines": ["", host_last, lidar_last]}
        if self.task_id == "kcan": return {"input_lines": ["yes" if self.kcan_remove_pcan.isChecked() else "no"]}
        return {"input_lines": []}

    def _validated_suffix(self, value: str, label: str) -> str | None:
        value = value.strip()
        if not value.isdigit() or not 0 <= int(value) <= 255: self.validation_message = f"{label}尾段必须是 0 到 255 的数字。"; return None
        return value

    @staticmethod
    def _suffix_field() -> QLineEdit:
        field = QLineEdit(); field.setValidator(QIntValidator(0, 999, field)); field.setMaxLength(3); return field

    def _title_for_task(self) -> str:
        return {"environment": "环境安装", "compile": "工作空间编译", "kcan": "KCAN 驱动", "vr": "VR 软件", "camera": "相机配置", "lidar": "雷达配置", "sync-source": "下载与更新"}.get(self.task_id, "部署任务")

    def _subtitle_for_task(self) -> str:
        return {"environment": "安装系统软件包与 Python 依赖", "compile": "重新编译 OpenFlex ROS 2 软件包", "kcan": "编译并安装 KCAN 内核驱动", "vr": "向头显安装对应的 OpenFlex VR 软件", "camera": "更新四个相机的序列号配置", "lidar": "修改 MID360 网络配置", "sync-source": "逐组件检查版本并下载或更新源码"}.get(self.task_id, "确认参数后继续")

    def _task_note(self) -> str:
        return {"environment": "此任务会修改系统软件包和 Python 环境。执行过程中可能需要 sudo 授权。", "compile": "编译会占用处理器资源，期间相关 ROS 2 节点可能不可用。", "sync-source": "脚本会逐个组件检查远程 revision；缺失或有更新时在终端确认，更新前自动备份旧组件。"}.get(self.task_id, "此任务不需要额外参数，确认后将直接执行。")

    def _apply_dialog_style(self):
        self.setStyleSheet("""
            QDialog#deploymentConfigDialog { background: #ffffff; color: #17232b; }
            QFrame#dialogHeader, QFrame#dialogFooter { background: #ffffff; border-bottom: 1px solid #dbe3e8; }
            QFrame#dialogFooter { border-top: 1px solid #dbe3e8; border-bottom: 0; }
            QLabel#dialogTitle { color: #17232b; font-size: 20px; font-weight: 700; }
            QLabel#dialogSubtitle { color: #6b7881; font-size: 12px; }
            QPushButton#dialogCloseButton { background: transparent; border: 0; color: #6b7881; font-size: 22px; }
            QLabel[objectName^="dialogStep"] { color: #9ba8ae; font-size: 11px; padding: 5px 0; border-bottom: 2px solid #dbe3e8; }
            QLabel[objectName^="dialogStep"][state="active"] { color: #0d7a78; font-weight: 700; border-bottom-color: #0d7a78; }
            QLabel[objectName^="dialogStep"][state="done"] { color: #0d7a78; border-bottom-color: #83c9c3; }
            QFrame#dialogBody { background: #ffffff; }
            QLabel#dialogNotice { background: #e4f3f1; color: #275b5b; border-left: 3px solid #0d7a78; padding: 10px; }
            QLabel#dialogNotice[kind="warning"] { background: #fff3df; color: #77501e; border-left-color: #c77a18; }
            QLabel#dialogNotice[kind="error"] { background: #fff0ef; color: #86433f; border-left-color: #b64b4b; }
            QFrame#dialogSummaryRow { background: #f8fafb; border: 1px solid #dbe3e8; border-radius: 6px; }
            QLabel#dialogSummaryLabel { color: #6b7881; }
            QLabel#dialogSummaryValue { color: #17232b; font-weight: 600; }
            QLabel#dialogProgress { color: #0d7a78; font-weight: 700; padding: 11px 0; border-bottom: 6px solid #dcebea; }
            QLabel#dialogExecutionLog { background: #142831; color: #d4e5e5; padding: 13px; border-radius: 6px; }
            QLabel#dialogResultMessage { color: #368862; font-size: 14px; font-weight: 700; }
            QLineEdit, QComboBox { min-height: 34px; padding: 0 9px; border: 1px solid #c7d2d9; border-radius: 6px; background: #ffffff; color: #17232b; }
            QCheckBox { color: #455761; spacing: 8px; }
            QPushButton { min-height: 34px; padding: 0 14px; border: 1px solid #c7d2d9; border-radius: 6px; background: #ffffff; color: #17232b; font-weight: 600; }
            QPushButton[variant="primary"] { background: #0d7a78; border-color: #0d7a78; color: #ffffff; }
            QPushButton[variant="danger"] { background: #fff0ef; border-color: #e0a5a0; color: #b64b4b; }
        """)
