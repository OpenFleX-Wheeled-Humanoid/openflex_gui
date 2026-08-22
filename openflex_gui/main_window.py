#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
OpenFlex VR 全身控制上位机
管理 CAN 总线、检查电机状态、启动整机控制和 VR 遥操作。
"""

import sys
import os
import signal
import subprocess
import socket
import struct
import time
import threading

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QGroupBox, QSizePolicy, QFrame, QCheckBox,
    QToolButton, QStyle, QStackedWidget, QScrollArea, QSplitter
)
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QSettings, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor, QTextCursor, QIcon

from .motor_manager_adapter import MotorManagerAdapter
from .theme_manager import ThemeManager

# ─── 项目路径 ──────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ICON_FILE = os.path.join(_SCRIPT_DIR, 'openflex_vr.png')


def _find_workspace_dir(start_dir: str) -> str:
    """Find the Openflex workspace from either source or installed package paths."""
    current = os.path.abspath(start_dir)
    while True:
        if (
            os.path.exists(os.path.join(current, 'install', 'setup.bash'))
            and os.path.isdir(os.path.join(current, 'src'))
        ):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(os.path.join(_SCRIPT_DIR, '..', '..', '..', '..'))
        current = parent


_WORKSPACE_DIR = _find_workspace_dir(_SCRIPT_DIR)
_SRC_DIR = os.path.join(_WORKSPACE_DIR, 'src')
_SETUP_BASH = os.path.join(_WORKSPACE_DIR, 'install', 'setup.bash')
_CAN_HELPER = os.path.join(_SCRIPT_DIR, 'enable_can_helper.sh')
_DISABLE_CAN_HELPER = os.path.join(_SCRIPT_DIR, 'disable_can_helper.sh')
_BATTERY_SERIAL_HELPER = os.path.join(_SCRIPT_DIR, 'enable_battery_serial_helper.sh')
_CAMERA_CONFIG = os.path.join(
    _WORKSPACE_DIR, 'src', 'openflex_vla', 'config', 'cameras', 'cameras_config_30fps.yaml'
)
_REALSENSE_VIEWER = '/usr/bin/realsense-viewer'

# 将 can_utils / check_motor_status 所在目录加入 path
def _find_motor_scripts_dir() -> str:
    candidates = (
        os.path.join(_SRC_DIR, 'openflex_integrated', 'openflex_manager', 'scripts'),
        os.path.join(_SRC_DIR, 'openflex_armx', 'openarmx_motor_manager', 'scripts'),
    )
    for path in candidates:
        if os.path.exists(os.path.join(path, 'can_utils.py')):
            return path
    return candidates[0]


_MOTOR_SCRIPTS_DIR = _find_motor_scripts_dir()
if _MOTOR_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _MOTOR_SCRIPTS_DIR)


# ─── CAN 配置（与 en_all_can.py 一致）──────────────────────────────
ROBOT_CAN_CONFIG = {
    'can0': 1000000,   # 右臂 Robstride
    'can1': 1000000,   # 左臂 Robstride
    'can2': 1000000,   # 头部 Robstride
    'can3': 1000000,   # 升降台 CANopen
    'can4': 1000000,   # 底盘驱动 UM 轮毂电机
    'can5': 1000000,   # 底盘转向 RS06
}


# ─── 信号桥（子线程 → GUI 线程）───────────────────────────────────
class _Signals(QObject):
    log = Signal(str)
    log_html = Signal(str)
    ui = Signal(object)


# ─── 状态指示灯 Widget ────────────────────────────────────────────
class StatusDot(QLabel):
    """小圆点状态指示：灰=空闲  绿=运行  红=异常"""
    _COLORS = {
        'idle':    '#888888',
        'running': '#2ecc71',
        'error':   '#e74c3c',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self.status_label = None
        self._state = 'idle'
        self._state_texts = {
            'idle': '待启动',
            'running': '运行中',
            'error': '异常',
        }
        self.set_state('idle')

    def bind_status_label(self, label: QLabel, state_texts: dict):
        self.status_label = label
        self._state_texts = state_texts
        label.setText(state_texts.get(self._state, self._state))

    def set_state(self, state: str):
        self._state = state
        c = self._COLORS.get(state, self._COLORS['idle'])
        self.setStyleSheet(
            f"background-color: {c}; border-radius: 8px; border: 1px solid #555;"
        )
        if self.status_label is not None:
            self.status_label.setText(self._state_texts.get(state, state))


# ─── 主窗口 ──────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, motor_adapter_factory=None):
        super().__init__()
        self.setWindowTitle('OpenFlex VR 全身控制上位机')
        self.setMinimumSize(1024, 700)
        self.resize(1360, 820)
        self.setWindowIcon(QIcon(_ICON_FILE))
        self._signals = _Signals()
        self._signals.log.connect(self._append_log)
        self._signals.log_html.connect(self._append_log_html)
        self._signals.ui.connect(self._run_ui_callback)
        self.theme_manager = ThemeManager(settings)
        self._motor_adapter_factory = motor_adapter_factory or MotorManagerAdapter
        self.motor_manager_adapter = None
        self.motor_page = None

        # 子进程管理
        self._proc_bringup: QProcess | None = None
        self._proc_battery: QProcess | None = None
        self._battery_stop_requested = False
        self._proc_vr: QProcess | None = None
        self._proc_camera: QProcess | None = None
        self._proc_camera_ros: QProcess | None = None
        self._proc_lidar: QProcess | None = None
        self._start_sequence_after_can = False
        self._auto_start_vr_pending = False
        self._auto_start_vr_attempts = 0
        self._auto_start_vr_max_attempts = 20

        self._build_ui()
        self._apply_theme()

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        central.setObjectName('appRoot')
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName('topBar')
        topbar.setFixedHeight(60)
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(20, 0, 22, 0)
        topbar_layout.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setObjectName('brandIcon')
        brand_icon.setFixedSize(34, 34)
        brand_icon.setAlignment(Qt.AlignCenter)
        brand_icon.setPixmap(QIcon(_ICON_FILE).pixmap(28, 28))
        title = QLabel('OpenFlex 控制中心')
        title.setObjectName('appTitle')
        title.setFont(QFont('Sans', 12, QFont.Bold))
        health = QLabel('● 控制台就绪')
        health.setObjectName('healthStatus')
        platform = QLabel('ROS 2 · KCAN · 发售版')
        platform.setObjectName('platformLabel')
        self.btn_theme = QToolButton()
        self.btn_theme.setObjectName('themeToggleButton')
        self.btn_theme.setFixedSize(34, 34)
        self.btn_theme.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.btn_theme.clicked.connect(self._toggle_theme)
        avatar = QLabel('OF')
        avatar.setObjectName('avatar')
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFixedSize(28, 28)
        topbar_layout.addWidget(brand_icon)
        topbar_layout.addWidget(title)
        topbar_layout.addStretch(1)
        topbar_layout.addWidget(health)
        topbar_layout.addSpacing(10)
        topbar_layout.addWidget(platform)
        topbar_layout.addWidget(self.btn_theme)
        topbar_layout.addWidget(avatar)
        root.addWidget(topbar)

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root.addWidget(body, 1)

        sidebar = QFrame()
        sidebar.setObjectName('sideBar')
        sidebar.setFixedWidth(218)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 18, 12, 18)
        sidebar_layout.setSpacing(6)
        workspace_label = QLabel('工作空间')
        workspace_label.setObjectName('workspaceLabel')
        sidebar_layout.addWidget(workspace_label)

        self.nav_buttons = []
        for index, text in enumerate(('整机控制', '电机管理', '传感器', '部署中心')):
            button = QPushButton(text)
            button.setObjectName('navButton')
            button.setCheckable(True)
            button.setMinimumHeight(38)
            button.clicked.connect(lambda checked=False, page=index: self._set_page(page))
            self.nav_buttons.append(button)
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch(1)
        side_footer = QLabel('OpenFlex VR 全身控制上位机\nROS 后台进程统一管理')
        side_footer.setObjectName('sideFooter')
        side_footer.setWordWrap(True)
        sidebar_layout.addWidget(side_footer)
        body_layout.addWidget(sidebar)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName('pageStack')
        body_layout.addWidget(self.page_stack, 1)

        self.page_stack.addWidget(self._scroll_page(self._build_control_page()))
        self.motor_page = self._build_motor_management_page()
        self.page_stack.addWidget(self.motor_page)
        self.page_stack.addWidget(self._scroll_page(self._build_sensors_page()))
        self.page_stack.addWidget(self._scroll_page(self._build_deploy_page()))
        self._set_page(0)

        self._refresh_can_ui_state()

    def _set_page(self, index: int):
        if self.page_stack.currentIndex() == 1 and index != 1:
            self._release_motor_management()
        if index == 1:
            self._activate_motor_management()
        self.page_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.nav_buttons):
            button.setChecked(button_index == index)

    def _release_motor_management(self):
        old_adapter = self.motor_manager_adapter
        old_page = self.motor_page
        if old_adapter is not None:
            old_adapter.shutdown()

        replacement = self._build_motor_management_page()
        self.page_stack.removeWidget(old_page)
        self.page_stack.insertWidget(1, replacement)
        self.motor_page = replacement
        old_page.deleteLater()
        self._log('已退出电机维护模式并释放直接硬件连接')

    def _activate_motor_management(self):
        adapter = self.motor_manager_adapter
        if adapter is None:
            return
        processes = (self._proc_bringup, self._proc_vr)
        if any(
            proc is not None and proc.state() != QProcess.NotRunning
            for proc in processes
        ):
            self._log_err('电机管理暂不可用：请先停止整机控制和 VR 遥操作')
            self._update_motor_page_lock()
            return
        try:
            adapter.initialize_controller()
            self.motor_page.setEnabled(True)
            self.motor_page.setToolTip('')
        except Exception as exc:
            self.motor_page.setEnabled(False)
            self.motor_page.setToolTip(f'电机控制器初始化失败: {exc}')
            self._log_err(f'电机控制器初始化失败: {exc}')

    def _build_motor_management_page(self) -> QWidget:
        manager_dir = os.path.join(
            _SRC_DIR, 'openflex_integrated', 'openflex_manager'
        )
        try:
            adapter = self._motor_adapter_factory(manager_dir)
            adapter.set_theme(self.theme_manager.current_theme)
            page = adapter.create_page()
            if hasattr(page, 'btn_theme_toggle'):
                page.btn_theme_toggle.hide()
            self.motor_manager_adapter = adapter
            return page
        except Exception as exc:
            self.motor_manager_adapter = None
            self._log_err(f'电机管理加载失败: {exc}')
            error_page = self._placeholder_page(
                '电机管理不可用',
                f'无法加载 {manager_dir}\n{exc}',
            )
            error_page.setObjectName('motorManagementErrorPage')
            return error_page

    @staticmethod
    def _scroll_page(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName('pageScroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    @staticmethod
    def _placeholder_page(title: str, description: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 26, 28, 28)
        heading = QLabel(title)
        heading.setFont(QFont('Sans', 18, QFont.Bold))
        layout.addWidget(heading)
        layout.addWidget(QLabel(description))
        layout.addStretch(1)
        return page

    def _apply_theme(self):
        stylesheet = """
            QMainWindow, QWidget#appRoot {
                background: #eef2f7;
                color: #1b2839;
                font-family: "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
                font-size: 12px;
            }
            QFrame#topBar {
                background: #ffffff;
                border-bottom: 1px solid #d4deea;
            }
            QLabel#brandIcon {
                background: #ffffff;
                border: 1px solid #d4deea;
                border-radius: 6px;
            }
            QLabel#appTitle {
                color: #1b2839;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#healthStatus {
                color: #16836e;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#platformLabel, QLabel#sideFooter, QLabel#pageSubtitle,
            QLabel#panelCopy, QLabel#cardCopy, QLabel#controlStepCopy {
                color: #708096;
            }
            QLabel#avatar {
                background: #e7edf7;
                color: #34527f;
                border-radius: 14px;
                font-size: 10px;
                font-weight: 700;
            }
            QToolButton#themeToggleButton {
                background: transparent;
                color: #53647a;
                border: 1px solid #d4deea;
                border-radius: 5px;
                font-size: 17px;
                font-weight: 600;
            }
            QToolButton#themeToggleButton:hover {
                background: #f2f5fa;
            }
            QFrame#sideBar {
                background: #ffffff;
                border-right: 1px solid #d4deea;
            }
            QLabel#workspaceLabel {
                color: #8592a4;
                font-size: 10px;
                font-weight: 700;
                padding: 0 8px 6px 8px;
            }
            QLabel#sideFooter {
                font-size: 10px;
                line-height: 1.5;
                padding: 10px 8px;
            }
            QPushButton#navButton {
                background: transparent;
                color: #607087;
                border: none;
                border-radius: 5px;
                text-align: left;
                padding-left: 14px;
                font-weight: 500;
            }
            QPushButton#navButton:hover {
                background: #f2f5fa;
            }
            QPushButton#navButton:checked {
                background: #e8efff;
                color: #1751c6;
                font-weight: 700;
            }
            QStackedWidget#pageStack, QScrollArea#pageScroll,
            QScrollArea#pageScroll > QWidget > QWidget {
                background: #eef2f7;
                border: none;
            }
            QLabel#pageTitle {
                color: #1b2839;
                font-size: 27px;
                font-weight: 700;
            }
            QFrame#controlWorkflow, QFrame#vrCard, QFrame#sensorCard,
            QFrame#deployPlaceholder {
                background: #ffffff;
                border: 1px solid #d4deea;
                border-radius: 7px;
            }
            QFrame#panelHeader {
                background: #ffffff;
                border: none;
                border-bottom: 1px solid #dbe5f1;
            }
            QFrame#controlStep {
                background: #ffffff;
                border: none;
                border-bottom: 1px solid #dbe5f1;
            }
            QFrame#controlStep[primaryStep="true"] {
                background: #f7faff;
            }
            QLabel#panelTitle, QLabel#cardTitle, QLabel#logTitle {
                color: #1b2839;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#kicker {
                color: #7358a5;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#stepNumber {
                background: #e8efff;
                color: #1751c6;
                border-radius: 15px;
                font-weight: 700;
            }
            QPushButton {
                min-height: 34px;
                padding: 0 12px;
                background: #ffffff;
                color: #41526a;
                border: 1px solid #d4deea;
                border-radius: 5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #f2f5fa;
            }
            QPushButton:disabled, QToolButton:disabled {
                color: #a0acbb;
                background: #eef1f5;
                border-color: #dce3eb;
            }
            QPushButton[variant="primary"] {
                background: #1e5bd3;
                color: #ffffff;
                border-color: #1e5bd3;
            }
            QPushButton[variant="primary"]:hover {
                background: #174fb9;
            }
            QPushButton[variant="danger"], QToolButton[variant="danger"] {
                background: #f6e6e6;
                color: #ad3838;
                border: 1px solid #eed3d3;
                border-radius: 5px;
            }
            QPushButton[variant="text"] {
                background: transparent;
                border: none;
                color: #8fa4bb;
                padding: 0 5px;
            }
            QPushButton[variant="primary"]:disabled,
            QPushButton[variant="danger"]:disabled,
            QPushButton[variant="secondary"]:disabled,
            QToolButton[variant="danger"]:disabled {
                color: #a0acbb;
                background: #eef1f5;
                border-color: #dce3eb;
            }
            QCheckBox {
                color: #53647a;
                spacing: 7px;
            }
            QFrame#runtimeLogCard {
                background: #172235;
                border: 1px solid #263750;
                border-radius: 7px;
            }
            QFrame#runtimeLogCard QLabel#logTitle {
                color: #f2f6fb;
            }
            QTextEdit#runtimeLog {
                background: #172235;
                color: #70dca5;
                border: none;
                selection-background-color: #31527e;
            }
            QSplitter::handle {
                background: transparent;
                width: 12px;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #e7edf4;
                border: none;
            }
            QScrollBar::handle:vertical {
                min-height: 28px;
                background: #aebccd;
                border-radius: 5px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """
        if self.theme_manager.current_theme == 'night':
            stylesheet += """
                QMainWindow, QWidget#appRoot {
                    background: #111827;
                    color: #e7edf5;
                }
                QFrame#topBar, QFrame#sideBar,
                QFrame#controlWorkflow, QFrame#vrCard, QFrame#sensorCard,
                QFrame#deployPlaceholder, QFrame#panelHeader,
                QFrame#controlStep, QFrame#controlStep[primaryStep="true"] {
                    background: #182235;
                    border-color: #2c3a4f;
                }
                QFrame#topBar {
                    border-bottom-color: #2c3a4f;
                }
                QFrame#sideBar {
                    border-right-color: #2c3a4f;
                }
                QFrame#panelHeader, QFrame#controlStep {
                    border-bottom-color: #2c3a4f;
                }
                QLabel#brandIcon {
                    background: #202c40;
                    border-color: #38485f;
                }
                QLabel#appTitle, QLabel#pageTitle, QLabel#panelTitle,
                QLabel#cardTitle, QLabel#controlStepTitle, QLabel#statusText {
                    color: #edf3fa;
                }
                QFrame#runtimeLogCard QLabel#logTitle {
                    color: #f2f6fb;
                }
                QLabel#platformLabel, QLabel#sideFooter, QLabel#pageSubtitle,
                QLabel#panelCopy, QLabel#cardCopy, QLabel#controlStepCopy,
                QCheckBox {
                    color: #9eacc0;
                }
                QLabel#workspaceLabel {
                    color: #8290a5;
                }
                QLabel#avatar {
                    background: #29364b;
                    color: #c9d7ea;
                }
                QPushButton#navButton {
                    color: #aab7c9;
                }
                QPushButton#navButton:hover {
                    background: #202c40;
                }
                QPushButton#navButton:checked {
                    background: #20385f;
                    color: #8eb5ff;
                }
                QStackedWidget#pageStack, QScrollArea#pageScroll,
                QScrollArea#pageScroll > QWidget > QWidget {
                    background: #111827;
                }
                QLabel#stepNumber {
                    background: #233c65;
                    color: #a8c6ff;
                }
                QPushButton, QToolButton#themeToggleButton {
                    background: #202c40;
                    color: #d9e3ef;
                    border-color: #3a4a61;
                }
                QPushButton:hover, QToolButton#themeToggleButton:hover {
                    background: #29374d;
                }
                QPushButton:disabled, QToolButton:disabled {
                    color: #657287;
                    background: #192233;
                    border-color: #2b374a;
                }
                QPushButton[variant="primary"] {
                    background: #3574e8;
                    border-color: #3574e8;
                    color: #ffffff;
                }
                QPushButton[variant="primary"]:hover {
                    background: #2866d5;
                }
                QPushButton[variant="danger"], QToolButton[variant="danger"] {
                    background: #46282f;
                    color: #ffb4b4;
                    border-color: #69404a;
                }
                QPushButton[variant="text"] {
                    background: transparent;
                    color: #9eb0c7;
                    border: none;
                }
                QScrollBar:vertical {
                    background: #182235;
                }
                QScrollBar::handle:vertical {
                    background: #53627a;
                }
            """
        self.setStyleSheet(stylesheet)
        self._update_theme_button()
        if self.motor_manager_adapter is not None:
            self.motor_manager_adapter.set_theme(self.theme_manager.current_theme)

    def _toggle_theme(self):
        self.theme_manager.toggle()
        self._apply_theme()

    def _update_theme_button(self):
        switching_to_night = self.theme_manager.current_theme == 'day'
        self.btn_theme.setIcon(QIcon())
        self.btn_theme.setText('☾' if switching_to_night else '☀')
        self.btn_theme.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_theme.setToolTip(
            '切换到夜间模式' if switching_to_night else '切换到日间模式'
        )

    def _build_control_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName('controlPage')
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        title = QLabel('整机控制')
        title.setObjectName('pageTitle')
        title.setFont(QFont('Sans', 22, QFont.Bold))
        subtitle = QLabel('按启动顺序完成 CAN、执行器检查，再启动整机控制。')
        subtitle.setObjectName('pageSubtitle')
        root.addWidget(title)
        root.addWidget(subtitle)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        workflow = QFrame()
        workflow.setObjectName('controlWorkflow')
        workflow.setMinimumWidth(430)
        workflow_layout = QVBoxLayout(workflow)
        workflow_layout.setContentsMargins(0, 0, 0, 0)
        workflow_layout.setSpacing(0)

        workflow_header = QFrame()
        workflow_header.setObjectName('panelHeader')
        workflow_header_layout = QVBoxLayout(workflow_header)
        workflow_header_layout.setContentsMargins(20, 17, 20, 16)
        workflow_header_layout.setSpacing(5)
        workflow_title = QLabel('整机控制')
        workflow_title.setObjectName('panelTitle')
        workflow_title.setFont(QFont('Sans', 15, QFont.Bold))
        workflow_copy = QLabel('按顺序准备底层接口、执行器和控制器')
        workflow_copy.setObjectName('panelCopy')
        workflow_header_layout.addWidget(workflow_title)
        workflow_header_layout.addWidget(workflow_copy)
        workflow_layout.addWidget(workflow_header)

        self.dot_can = StatusDot()
        self.btn_enable_can = self._button('启用全部 CAN', 'primary')
        self.btn_enable_can.clicked.connect(self._on_enable_can)
        self.btn_disable_can = self._button('禁用全部', 'danger')
        self.btn_disable_can.clicked.connect(self._on_disable_can)
        workflow_layout.addWidget(self._build_control_step(
            '1', 'CAN 总线', '先启用并确认 can0–can5 接口', self.dot_can,
            (self.btn_enable_can, self.btn_disable_can),
            {'idle': '待启动', 'running': '通道在线', 'error': '接口异常'},
        ))

        self.dot_status = StatusDot()
        self.btn_status = self._button('检查全部状态', 'secondary')
        self.btn_status.clicked.connect(self._on_check_status)
        workflow_layout.addWidget(self._build_control_step(
            '2', '电机状态', '检查全部控制器、节点和电机反馈', self.dot_status,
            (self.btn_status,),
            {'idle': '待检查', 'running': '检查中', 'error': '状态异常'},
        ))

        self.dot_bringup = StatusDot()
        self.btn_bringup_start = self._button('启动整机控制', 'primary')
        self.btn_bringup_start.clicked.connect(self._on_start_bringup)
        self.btn_bringup_stop = self._button('停止整机', 'danger')
        self.btn_bringup_stop.setEnabled(False)
        self.btn_bringup_stop.clicked.connect(self._on_stop_bringup)
        workflow_layout.addWidget(self._build_control_step(
            '3', '启动整机控制', '加载全部控制器并进入可操作状态', self.dot_bringup,
            (self.btn_bringup_start, self.btn_bringup_stop),
            {'idle': '待启动', 'running': '运行中', 'error': '启动异常'},
            primary=True,
        ))
        workflow_layout.addStretch(1)

        left_column = QFrame()
        left_column.setObjectName('controlLeftColumn')
        left_column.setMinimumWidth(430)
        left_column_layout = QVBoxLayout(left_column)
        left_column_layout.setContentsMargins(0, 0, 0, 0)
        left_column_layout.setSpacing(12)
        left_column_layout.addWidget(workflow, 1)

        right_column = QFrame()
        right_column.setObjectName('controlRightColumn')
        right_column.setMinimumWidth(280)
        right_column_layout = QVBoxLayout(right_column)
        right_column_layout.setContentsMargins(0, 0, 0, 0)
        right_column_layout.setSpacing(0)

        vr_card = QFrame()
        vr_card.setObjectName('vrCard')
        vr_layout = QVBoxLayout(vr_card)
        vr_layout.setContentsMargins(18, 17, 18, 18)
        vr_layout.setSpacing(9)
        vr_kicker = QLabel('OPENXR')
        vr_kicker.setObjectName('kicker')
        vr_title = QLabel('VR 遥操作')
        vr_title.setObjectName('cardTitle')
        vr_copy = QLabel('启动 VR 控制，并选择是否接收底盘速度。')
        vr_copy.setObjectName('cardCopy')
        vr_copy.setWordWrap(True)
        self.dot_vr = StatusDot()
        vr_status_row = QHBoxLayout()
        vr_status = QLabel()
        vr_status.setObjectName('statusText')
        self.dot_vr.bind_status_label(vr_status, {
            'idle': '设备待启动',
            'running': '运行中',
            'error': '连接异常',
        })
        vr_status_row.addWidget(self.dot_vr)
        vr_status_row.addWidget(vr_status)
        vr_status_row.addStretch(1)
        self.chk_vr_chassis = QCheckBox('VR 控制底盘速度')
        self.chk_vr_chassis.setChecked(True)
        self.chk_vr_chassis.setToolTip('勾选后使用 VR 发来的底盘线速度/角速度上限')
        self.btn_vr_start = self._button('启动 VR', 'primary')
        self.btn_vr_start.clicked.connect(self._on_start_vr)
        self.btn_vr_stop = self._button('停止', 'secondary')
        self.btn_vr_stop.setEnabled(False)
        self.btn_vr_stop.clicked.connect(self._on_stop_vr)
        vr_actions = QHBoxLayout()
        vr_actions.addWidget(self.btn_vr_start)
        vr_actions.addWidget(self.btn_vr_stop)
        vr_actions.addStretch(1)
        vr_layout.addWidget(vr_kicker)
        vr_layout.addWidget(vr_title)
        vr_layout.addWidget(vr_copy)
        vr_layout.addLayout(vr_status_row)
        vr_layout.addWidget(self.chk_vr_chassis)
        vr_layout.addLayout(vr_actions)
        left_column_layout.addWidget(vr_card)

        log_card = QFrame()
        log_card.setObjectName('runtimeLogCard')
        log_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(15, 14, 15, 15)
        log_header = QHBoxLayout()
        log_title = QLabel('运行诊断')
        log_title.setObjectName('logTitle')
        log_title.setMinimumWidth(72)
        btn_clear = self._button('清空', 'text')
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        log_header.addWidget(btn_clear)
        self.log_view = QTextEdit()
        self.log_view.setObjectName('runtimeLog')
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(3000)
        self.log_view.setFont(QFont('Monospace', 9))
        self.log_view.setTextColor(QColor('#70dca5'))
        self.log_view.setMinimumHeight(220)
        btn_clear.clicked.connect(self.log_view.clear)
        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log_view, 1)
        right_column_layout.addWidget(log_card, 1)
        splitter.addWidget(left_column)
        splitter.addWidget(right_column)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        return page

    def _build_sensors_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName('sensorsPage')
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)

        title = QLabel('传感器')
        title.setObjectName('pageTitle')
        title.setFont(QFont('Sans', 22, QFont.Bold))
        subtitle = QLabel('查看 RealSense 相机和 Livox MID360S 激光雷达。')
        subtitle.setObjectName('pageSubtitle')
        root.addWidget(title)
        root.addWidget(subtitle)

        camera_card = QFrame()
        camera_card.setObjectName('sensorCard')
        camera_layout = QVBoxLayout(camera_card)
        camera_layout.setContentsMargins(20, 18, 20, 20)
        camera_layout.setSpacing(10)
        camera_title = QLabel('RealSense 相机')
        camera_title.setObjectName('cardTitle')
        camera_copy = QLabel('启动四路 RGB ROS2/RViz2 视图，或打开独立 RealSense Viewer。')
        camera_copy.setObjectName('cardCopy')
        camera_copy.setWordWrap(True)
        camera_actions = QHBoxLayout()
        camera_actions.setSpacing(8)
        self.btn_camera_ros = self._button('相机查看 · ROS2', 'primary')
        self.btn_camera_ros.setToolTip('启动四路 RGB 相机并在 RViz2 中显示')
        self.btn_camera_ros.clicked.connect(self._on_start_camera_ros)
        self.btn_camera = self._button('相机查看 · Viewer', 'secondary')
        self.btn_camera.clicked.connect(self._on_start_camera_viewer)
        self.btn_camera_stop = QToolButton()
        self.btn_camera_stop.setProperty('variant', 'danger')
        self.btn_camera_stop.setIcon(self.style().standardIcon(QStyle.SP_MediaStop))
        self.btn_camera_stop.setFixedSize(40, 40)
        self.btn_camera_stop.setToolTip('停止当前 RealSense 相机查看')
        self.btn_camera_stop.setEnabled(False)
        self.btn_camera_stop.clicked.connect(self._on_stop_camera_viewer)
        camera_actions.addWidget(self.btn_camera_ros)
        camera_actions.addWidget(self.btn_camera)
        camera_actions.addWidget(self.btn_camera_stop)
        camera_actions.addStretch(1)
        camera_layout.addWidget(camera_title)
        camera_layout.addWidget(camera_copy)
        camera_layout.addLayout(camera_actions)
        root.addWidget(camera_card)

        lidar_card = QFrame()
        lidar_card.setObjectName('sensorCard')
        lidar_layout = QVBoxLayout(lidar_card)
        lidar_layout.setContentsMargins(20, 18, 20, 20)
        lidar_layout.setSpacing(10)
        lidar_title = QLabel('Livox MID360S')
        lidar_title.setObjectName('cardTitle')
        lidar_copy = QLabel('启动 Livox ROS2 驱动并打开对应 RViz2 配置。')
        lidar_copy.setObjectName('cardCopy')
        lidar_actions = QHBoxLayout()
        lidar_actions.setSpacing(8)
        self.btn_lidar = self._button('启动激光雷达查看器', 'primary')
        self.btn_lidar.clicked.connect(self._on_start_lidar_viewer)
        self.btn_lidar_stop = self._button('停止', 'danger')
        self.btn_lidar_stop.setEnabled(False)
        self.btn_lidar_stop.clicked.connect(self._on_stop_lidar_viewer)
        lidar_actions.addWidget(self.btn_lidar)
        lidar_actions.addWidget(self.btn_lidar_stop)
        lidar_actions.addStretch(1)
        lidar_layout.addWidget(lidar_title)
        lidar_layout.addWidget(lidar_copy)
        lidar_layout.addLayout(lidar_actions)
        root.addWidget(lidar_card)
        root.addStretch(1)
        return page

    @staticmethod
    def _build_deploy_page() -> QWidget:
        page = QWidget()
        page.setObjectName('deployPage')
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(16)
        title = QLabel('部署中心')
        title.setObjectName('pageTitle')
        title.setFont(QFont('Sans', 22, QFont.Bold))
        subtitle = QLabel('管理 OpenFlex 驱动安装和 ROS 2 工作区构建入口。')
        subtitle.setObjectName('pageSubtitle')
        placeholder = QFrame()
        placeholder.setObjectName('deployPlaceholder')
        placeholder_layout = QVBoxLayout(placeholder)
        placeholder_layout.setContentsMargins(22, 20, 22, 22)
        heading = QLabel('部署功能尚未接入')
        heading.setObjectName('cardTitle')
        copy = QLabel('基础面板仅保留页面位置。接入安装脚本前，不提供可能误触发系统变更的操作按钮。')
        copy.setObjectName('cardCopy')
        copy.setWordWrap(True)
        placeholder_layout.addWidget(heading)
        placeholder_layout.addWidget(copy)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(placeholder)
        root.addStretch(1)
        return page

    @staticmethod
    def _button(text: str, variant: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty('variant', variant)
        button.setMinimumHeight(36)
        return button

    @staticmethod
    def _build_control_step(number: str, title: str, description: str,
                            dot: StatusDot, buttons: tuple,
                            state_texts: dict,
                            primary: bool = False) -> QFrame:
        step = QFrame()
        step.setObjectName('controlStep')
        step.setProperty('primaryStep', primary)
        step.setMinimumHeight(116)
        layout = QHBoxLayout(step)
        layout.setContentsMargins(20, 15, 20, 15)
        layout.setSpacing(14)

        number_label = QLabel(number)
        number_label.setObjectName('stepNumber')
        number_label.setAlignment(Qt.AlignCenter)
        number_label.setFixedSize(30, 30)
        layout.addWidget(number_label, 0, Qt.AlignTop)

        copy_layout = QVBoxLayout()
        copy_layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName('controlStepTitle')
        title_label.setFont(QFont('Sans', 11, QFont.Bold))
        description_label = QLabel(description)
        description_label.setObjectName('controlStepCopy')
        description_label.setWordWrap(True)
        status_layout = QHBoxLayout()
        status_layout.setSpacing(6)
        status_label = QLabel()
        status_label.setObjectName('statusText')
        dot.bind_status_label(status_label, state_texts)
        status_layout.addWidget(dot)
        status_layout.addWidget(status_label)
        status_layout.addStretch(1)
        copy_layout.addWidget(title_label)
        copy_layout.addWidget(description_label)
        copy_layout.addLayout(status_layout)
        layout.addLayout(copy_layout, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        for button in buttons:
            button.setMinimumWidth(142)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return step

    # ── 日志 ─────────────────────────────────────────────────────
    def _append_log(self, text: str):
        self.log_view.append(text)
        self.log_view.moveCursor(QTextCursor.End)

    def _append_log_html(self, html: str):
        self.log_view.append(html)
        self.log_view.moveCursor(QTextCursor.End)

    def _log(self, msg: str):
        """线程安全日志"""
        self._signals.log.emit(msg)

    def _log_ok(self, msg: str):
        self._signals.log_html.emit(f'<span style="color:#2ecc71">{msg}</span>')

    def _log_err(self, msg: str):
        self._signals.log_html.emit(f'<span style="color:#e74c3c">{msg}</span>')

    def _call_ui(self, callback):
        self._signals.ui.emit(callback)

    @staticmethod
    def _run_ui_callback(callback):
        callback()

    # ── 1. CAN 总线 ─────────────────────────────────────────────
    def _on_enable_can(self):
        self._start_enable_can()

    def _start_enable_can(self, start_sequence: bool = False):
        self._start_sequence_after_can = start_sequence
        if start_sequence:
            self._auto_start_vr_pending = False
            self._auto_start_vr_attempts = 0
        self._set_can_buttons_enabled(False, False)
        self.dot_can.set_state('running')
        threading.Thread(target=self._enable_can_worker, daemon=True).start()

    def _enable_can_worker(self):
        self._log('=' * 50)
        self._log('启用全部 CAN 通道...')
        if not os.path.exists(_CAN_HELPER):
            self._log_err(f'CAN helper 不存在: {_CAN_HELPER}')
            self._start_sequence_after_can = False
            self._call_ui(self._refresh_can_ui_state)
            return

        try:
            ret = subprocess.run(
                ['bash', _CAN_HELPER],
                capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            self._log_err('CAN helper 执行超时')
            self._start_sequence_after_can = False
            self._call_ui(self._refresh_can_ui_state)
            return
        except Exception as e:
            self._log_err(f'执行 CAN helper 异常: {e}')
            self._start_sequence_after_can = False
            self._call_ui(self._refresh_can_ui_state)
            return

        output = (ret.stdout or '').strip()
        err_output = (ret.stderr or '').strip()
        if output:
            for line in output.splitlines():
                self._log(line)
        if err_output:
            for line in err_output.splitlines():
                self._log_err(line)

        if ret.returncode == 0:
            self._log_ok('全部 CAN 通道已启用')
            self._signals.log.emit('')  # trigger UI update
            self._call_ui(self._refresh_can_ui_state)
            if self._start_sequence_after_can:
                self._log('CAN 已就绪，继续启动整机控制...')
                self._call_ui(self._start_bringup_then_wait_for_vr)
        else:
            self._log_err(f'CAN helper 退出失败 (code={ret.returncode})')
            self._call_ui(self._refresh_can_ui_state)

        self._start_sequence_after_can = False

    def _on_disable_can(self):
        self._auto_start_vr_pending = False
        self._start_sequence_after_can = False
        self._set_can_buttons_enabled(False, False)
        self.dot_can.set_state('running')
        threading.Thread(target=self._disable_can_worker, daemon=True).start()

    def _disable_can_worker(self):
        self._log('=' * 50)
        self._log('禁用全部 CAN 通道...')
        if not os.path.exists(_DISABLE_CAN_HELPER):
            self._log_err(f'CAN disable helper 不存在: {_DISABLE_CAN_HELPER}')
            self._call_ui(self._refresh_can_ui_state)
            return

        try:
            ret = subprocess.run(
                ['bash', _DISABLE_CAN_HELPER],
                capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            self._log_err('CAN disable helper 执行超时')
            self._call_ui(self._refresh_can_ui_state)
            return
        except Exception as e:
            self._log_err(f'执行 CAN disable helper 异常: {e}')
            self._call_ui(self._refresh_can_ui_state)
            return

        output = (ret.stdout or '').strip()
        err_output = (ret.stderr or '').strip()
        if output:
            for line in output.splitlines():
                self._log(line)
        if err_output:
            for line in err_output.splitlines():
                self._log_err(line)

        if ret.returncode == 0:
            self._log_ok('全部 CAN 通道已禁用')
        else:
            self._log_err(f'CAN disable helper 退出失败 (code={ret.returncode})')
        self._call_ui(self._refresh_can_ui_state)

    def _set_can_buttons_enabled(self, enable_allowed: bool, disable_allowed: bool):
        self.btn_enable_can.setEnabled(enable_allowed)
        self.btn_disable_can.setEnabled(disable_allowed)

    def _refresh_can_ui_state(self):
        existing = [iface for iface in ROBOT_CAN_CONFIG if self._iface_exists(iface)]
        up_ifaces = [iface for iface in existing if self._iface_up(iface)]

        if not existing:
            self.dot_can.set_state('error')
            self._set_can_buttons_enabled(False, False)
        elif len(up_ifaces) == len(existing):
            self.dot_can.set_state('running')
            self._set_can_buttons_enabled(False, True)
        elif up_ifaces:
            self.dot_can.set_state('error')
            self._set_can_buttons_enabled(True, True)
        else:
            self.dot_can.set_state('idle')
            self._set_can_buttons_enabled(True, False)

    # ── 2. 检查电机状态 ──────────────────────────────────────────
    def _on_check_status(self):
        self.btn_status.setEnabled(False)
        self.dot_status.set_state('running')
        threading.Thread(target=self._check_status_worker, daemon=True).start()

    def _check_status_worker(self):
        self._log('=' * 50)
        self._log('检查全部电机状态...')

        # 使用 check_motor_status.py 脚本（不依赖 openflex_driver 包）
        script_path = os.path.join(
            _SRC_DIR, 'openflex_integrated', 'openflex_manager', 'scripts', 'check_motor_status.py'
        )

        if not os.path.exists(script_path):
            self._log_err(f'脚本不存在: {script_path}')
            self._call_ui(lambda: self.dot_status.set_state('idle'))
            self._call_ui(lambda: self.btn_status.setEnabled(True))
            return

        try:
            import subprocess
            result = subprocess.run(
                ['python3', script_path],
                capture_output=True,
                text=True,
                timeout=30
            )

            # 输出脚本结果
            if result.stdout:
                self._log(result.stdout.rstrip())
            if result.stderr:
                self._log_err(result.stderr.rstrip())

            all_ok = (result.returncode == 0)
        except subprocess.TimeoutExpired:
            self._log_err('检查超时（30秒）')
            all_ok = False
        except Exception as e:
            self._log_err(f'执行脚本异常: {e}')
            all_ok = False

        self._log('')
        if all_ok:
            self._log_ok('全部电机状态正常')
        else:
            self._log_err('部分电机状态异常或无响应')

        self._call_ui(lambda: self.dot_status.set_state('idle'))
        self._call_ui(lambda: self.btn_status.setEnabled(True))

    @staticmethod
    def _iface_exists(iface: str) -> bool:
        ret = subprocess.run(
            ['ip', 'link', 'show', iface],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return ret.returncode == 0

    @staticmethod
    def _iface_up(iface: str) -> bool:
        ret = subprocess.run(
            ['ip', 'link', 'show', iface],
            capture_output=True,
            text=True,
        )
        if ret.returncode != 0:
            return False
        first_line = ret.stdout.splitlines()[0] if ret.stdout else ''
        flags_start = first_line.find('<')
        flags_end = first_line.find('>', flags_start + 1)
        if flags_start == -1 or flags_end == -1:
            return False
        flags = first_line[flags_start + 1:flags_end].split(',')
        return 'UP' in flags

    def _start_bringup_then_wait_for_vr(self):
        self._on_start_bringup()
        self._auto_start_vr_pending = True
        self._auto_start_vr_attempts = 0
        QTimer.singleShot(3000, self._poll_bringup_ready_for_vr)

    def _poll_bringup_ready_for_vr(self):
        if not self._auto_start_vr_pending:
            return

        if self._proc_vr and self._proc_vr.state() != QProcess.NotRunning:
            self._auto_start_vr_pending = False
            return

        if self._proc_bringup is None or self._proc_bringup.state() == QProcess.NotRunning:
            self._log_err('整机控制未保持运行，已取消自动启动 VR')
            self._auto_start_vr_pending = False
            return

        if self._integrated_bringup_ready():
            self._log_ok('整机控制已就绪，自动启动 VR 遥操作')
            self._auto_start_vr_pending = False
            self._on_start_vr()
            return

        self._auto_start_vr_attempts += 1
        if self._auto_start_vr_attempts >= self._auto_start_vr_max_attempts:
            self._log_err('等待整机控制就绪超时，未自动启动 VR，请手动检查后启动')
            self._auto_start_vr_pending = False
            return

        QTimer.singleShot(3000, self._poll_bringup_ready_for_vr)

    def _integrated_bringup_ready(self) -> bool:
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 control list_controllers --controller-manager /controller_manager'
        )
        try:
            ret = subprocess.run(
                ['bash', '-c', cmd],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

        if ret.returncode != 0:
            return False

        output = ret.stdout or ''
        required_active = (
            'joint_state_broadcaster',
            'swerve_drive_controller',
            'velocity_controller',
            'left_forward_position_controller',
            'right_forward_position_controller',
            'head_forward_position_controller',
        )
        controller_states = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                controller_states[parts[0]] = parts[2].lower()
        for controller_name in required_active:
            if controller_states.get(controller_name) != 'active':
                return False
        return True

    def _motor_maintenance_active(self) -> bool:
        return bool(
            self.motor_manager_adapter is not None
            and self.motor_manager_adapter.has_active_connection()
        )

    def _update_motor_page_lock(self):
        processes = (self._proc_bringup, self._proc_vr)
        locked = any(
            proc is not None and proc.state() != QProcess.NotRunning
            for proc in processes
        )
        if self.motor_page is not None:
            self.motor_page.setEnabled(not locked)
            self.motor_page.setToolTip(
                '整机控制或 VR 运行期间不可使用直接电机管理'
                if locked else ''
            )

    # ── 3. 整机控制 ──────────────────────────────────────────────
    def _on_start_bringup(self):
        if self._proc_bringup and self._proc_bringup.state() != QProcess.NotRunning:
            self._log('整机控制已在运行中')
            return

        if self._motor_maintenance_active():
            self._log_err('整机控制启动已取消：电机管理仍有直接硬件连接，请先断开')
            self.dot_bringup.set_state('error')
            return

        unavailable_can = [
            iface for iface in ROBOT_CAN_CONFIG
            if not self._iface_exists(iface) or not self._iface_up(iface)
        ]
        if unavailable_can:
            self._log_err(
                '整机控制启动已取消，请先启用全部 CAN。'
                f'未就绪通道: {", ".join(unavailable_can)}'
            )
            self.dot_bringup.set_state('error')
            return

        self._log('=' * 50)
        self._log('启动整机控制...')

        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 launch openarmx_integrated_bringup integrated_robot_bringup.launch.py '
            'use_fake_hardware:=false '
            'chassis_steering_can:=can5 '
            'chassis_driving_can:=can4 '
            'left_arm_can:=can1 '
            'right_arm_can:=can0 '
            'lift_can:=can3 '
            'lift_node_id:=16 '
            'head_can:=can2 '
            'use_rviz:=true'
        )

        self._proc_bringup = self._launch_process(
            cmd, self.dot_bringup, self.btn_bringup_start, self.btn_bringup_stop, '整机控制'
        )
        if self._proc_bringup.state() != QProcess.NotRunning:
            self._update_motor_page_lock()
            self._start_battery_monitor()

    def _battery_node_is_running(self) -> bool:
        cmd = f'source {_SETUP_BASH} && ros2 node list'
        try:
            ret = subprocess.run(
                ['bash', '-c', cmd],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
        if ret.returncode != 0:
            return False
        battery_node_names = {
            'jd_battery_node',
            'jd_battery_multi_node',
            'jd_battery_master_dual_node',
        }
        return any(
            name.rstrip('/').rsplit('/', 1)[-1] in battery_node_names
            for name in ret.stdout.splitlines()
        )

    def _start_battery_monitor(self):
        if self._proc_battery and self._proc_battery.state() != QProcess.NotRunning:
            self._log('电池监控已由本界面启动')
            return
        if self._battery_node_is_running():
            self._log_ok('检测到电池监控节点已在运行，继续使用现有节点')
            return

        self._log('启动电池监控...')
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 launch openarmx_battery_monitor auto_pack_overlay.launch.py '
            'start_rviz:=false '
            'fix_serial_permission:=false'
        )
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))
        proc.finished.connect(
            lambda code, status: self._on_battery_finished(proc, code, status)
        )
        self._proc_battery = proc
        self._battery_stop_requested = False
        proc.start('setsid', ['--wait', 'bash', '-c', cmd])
        if not proc.waitForStarted(5000):
            self._log_err('电池监控启动失败，整机控制将继续运行')
            self._proc_battery = None
        else:
            self._log_ok(f'电池监控已启动 (PID: {proc.processId()})')

    def _stop_battery_monitor(self):
        proc = self._proc_battery
        if (
            proc is None
            or proc.state() == QProcess.NotRunning
            or self._battery_stop_requested
        ):
            return
        self._battery_stop_requested = True
        self._log('正在停止电池监控...')
        pid = proc.processId()
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGINT)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        else:
            proc.terminate()
        QTimer.singleShot(3000, lambda: self._force_kill(proc, '电池监控'))

    def _on_battery_finished(self, proc: QProcess, code, status):
        if self._proc_battery is proc:
            self._proc_battery = None
        was_stopping = self._battery_stop_requested
        self._battery_stop_requested = False
        if code == 0 or was_stopping:
            self._log('电池监控已退出')
        else:
            self._log_err(f'电池监控退出 (code={code})，整机控制继续运行')

    def _on_stop_bringup(self):
        self._auto_start_vr_pending = False
        # 先失能底盘（controller_manager 还在），再杀 bringup 进程
        self._log('正在失能底盘...')
        self._deactivate_chassis(then_stop_bringup=True)

    def _deactivate_chassis(self, then_stop_bringup=False):
        """通过 ros2 control 将底盘控制器切为 inactive，停止电机并关闭 CAN"""
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 control set_controller_state swerve_drive_controller inactive && '
            'ros2 control set_hardware_component_state swerve_drive_system inactive'
        )
        proc = subprocess.Popen(
            ['bash', '-c', cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        threading.Thread(
            target=self._wait_deactivate, args=(proc, then_stop_bringup),
            daemon=True
        ).start()

    def _wait_deactivate(self, proc, then_stop_bringup):
        try:
            _, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            self._signals.log.emit('[ERR] 底盘失能超时')
            if then_stop_bringup:
                QTimer.singleShot(0, self._do_stop_bringup)
            return

        if proc.returncode == 0:
            self._signals.log.emit('[OK] 底盘已失能')
        else:
            err = stderr.decode(errors='replace').strip()
            self._signals.log.emit(f'[ERR] 底盘失能失败: {err}')

        if then_stop_bringup:
            QTimer.singleShot(0, self._do_stop_bringup)

    def _do_stop_bringup(self):
        self._stop_battery_monitor()
        self._stop_process(self._proc_bringup, self.dot_bringup,
                           self.btn_bringup_start, self.btn_bringup_stop, '整机控制')

    # ── 4. VR 遥操作 ─────────────────────────────────────────────
    def _on_start_vr(self):
        self._auto_start_vr_pending = False
        if self._proc_vr and self._proc_vr.state() != QProcess.NotRunning:
            self._log('VR 遥操作已在运行中')
            return


        if self._motor_maintenance_active():
            self._log_err('VR 遥操作启动已取消：电机管理仍有直接硬件连接，请先断开')
            self.dot_vr.set_state('error')
            return

        self._log('=' * 50)
        self._log('启动 VR 遥操作...')

        vr_chassis = 'true' if self.chk_vr_chassis.isChecked() else 'false'
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 launch openarmx_integrated_bringup integrated_vr_teleop.launch.py '
            f'vr_chassis:={vr_chassis}'
        )

        self._proc_vr = self._launch_process(
            cmd, self.dot_vr, self.btn_vr_start, self.btn_vr_stop, 'VR 遥操作'
        )
        if self._proc_vr.state() != QProcess.NotRunning:
            self._update_motor_page_lock()

    def _on_stop_vr(self):
        self._auto_start_vr_pending = False
        self._stop_process(self._proc_vr, self.dot_vr,
                           self.btn_vr_start, self.btn_vr_stop, 'VR 遥操作')

    # ── 5. 传感器检测 (Ultra版) ────────────────────────────────────
    def _camera_process_running(self) -> bool:
        return any(
            proc is not None and proc.state() != QProcess.NotRunning
            for proc in (self._proc_camera_ros, self._proc_camera)
        )

    def _set_camera_buttons_enabled(self, enabled: bool):
        self.btn_camera_ros.setEnabled(enabled)
        self.btn_camera.setEnabled(enabled)
        self.btn_camera_stop.setEnabled(not enabled)

    @staticmethod
    def _terminate_camera_process(proc: QProcess, process_group: bool):
        if proc.state() == QProcess.NotRunning:
            return
        if process_group and proc.processId() > 0:
            try:
                os.killpg(os.getpgid(proc.processId()), signal.SIGTERM)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        proc.terminate()

    @staticmethod
    def _force_stop_camera_process(proc: QProcess, process_group: bool):
        if proc.state() == QProcess.NotRunning:
            return
        if process_group and proc.processId() > 0:
            try:
                os.killpg(os.getpgid(proc.processId()), signal.SIGKILL)
                return
            except ProcessLookupError:
                return
            except OSError:
                pass
        proc.kill()

    def _on_stop_camera_viewer(self):
        processes = (
            (self._proc_camera_ros, True),
            (self._proc_camera, False),
        )
        active = [
            (proc, process_group)
            for proc, process_group in processes
            if proc is not None and proc.state() != QProcess.NotRunning
        ]
        if not active:
            self._set_camera_buttons_enabled(True)
            return

        self._log('正在停止 RealSense 相机查看...')
        self.btn_camera_stop.setEnabled(False)
        for proc, process_group in active:
            self._terminate_camera_process(proc, process_group)
            QTimer.singleShot(
                5000,
                lambda proc=proc, process_group=process_group:
                    self._force_stop_camera_process(proc, process_group),
            )

    def _on_start_camera_ros(self):
        if self._camera_process_running():
            self._log('已有 RealSense 查看器在运行；请先关闭其窗口')
            return
        if not os.path.exists(_CAMERA_CONFIG):
            self._log_err(f'相机配置不存在: {_CAMERA_CONFIG}')
            return

        self._log('=' * 50)
        self._log('启动四路 RealSense RGB 与 RViz2...')
        self._set_camera_buttons_enabled(False)
        cmd = (
            'source /opt/ros/humble/setup.bash && '
            f'source {_SETUP_BASH} && '
            'ros2 launch openarmx_lerobot camera_rgb_viewer.launch.py '
            f'camera_config:={_CAMERA_CONFIG}'
        )
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))
        proc.finished.connect(
            lambda code, status: self._on_camera_ros_finished(proc, code, status)
        )
        proc.start('setsid', ['--wait', 'bash', '-c', cmd])
        if not proc.waitForStarted(5000):
            self._log_err('RealSense ROS2/RViz2 启动失败')
            self._set_camera_buttons_enabled(True)
        else:
            self._proc_camera_ros = proc
            self._log_ok(f'RealSense ROS2/RViz2 已启动 (PID: {proc.processId()})')

    def _on_camera_ros_finished(self, proc: QProcess, code, status):
        if self._proc_camera_ros is proc:
            self._proc_camera_ros = None
        if code == 0:
            self._log('RealSense ROS2/RViz2 已退出')
        else:
            self._log_err(f'RealSense ROS2/RViz2 退出 (code={code})')
        self._set_camera_buttons_enabled(True)

    def _on_start_camera_viewer(self):
        if self._camera_process_running():
            self._log('已有 RealSense 查看器在运行；请先关闭其窗口')
            return
        if not os.path.isfile(_REALSENSE_VIEWER):
            self._log_err(f'RealSense Viewer 不存在: {_REALSENSE_VIEWER}')
            return

        self._log('=' * 50)
        self._log('启动 RealSense 相机查看器（ROS Humble 版本）...')
        self._set_camera_buttons_enabled(False)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))
        proc.finished.connect(lambda code, status: self._on_camera_finished(code, status))

        # The console inherits ROS library paths. Keep this standalone Viewer
        # on its matching system SDK instead of loading the ROS SDK copy.
        viewer_env = QProcessEnvironment.systemEnvironment()
        viewer_env.remove('LD_LIBRARY_PATH')
        proc.setProcessEnvironment(viewer_env)
        proc.start(_REALSENSE_VIEWER)
        if not proc.waitForStarted(5000):
            self._log_err('RealSense Viewer 启动失败')
            self._set_camera_buttons_enabled(True)
        else:
            self._log_ok('RealSense Viewer 已启动')
            self._proc_camera = proc

    def _on_camera_finished(self, code, status):
        if code == 0:
            self._log('RealSense Viewer 已退出')
        else:
            self._log_err(f'RealSense Viewer 退出 (code={code})')
        self._set_camera_buttons_enabled(True)
        self._proc_camera = None

    def _on_start_lidar_viewer(self):
        if self._proc_lidar and self._proc_lidar.state() != QProcess.NotRunning:
            self._log('Livox 激光雷达查看器已在运行中')
            return

        self._log('=' * 50)
        self._log('启动 Livox Mid-360S 激光雷达查看器...')
        self.btn_lidar.setEnabled(False)
        self.btn_lidar_stop.setEnabled(True)

        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 launch livox_ros_driver2 rviz_MID360_launch.py'
        )

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_proc_output(proc))
        proc.finished.connect(lambda code, status: self._on_lidar_finished(code, status))

        proc.start('setsid', ['--wait', 'bash', '-c', cmd])
        if not proc.waitForStarted(5000):
            self._log_err('Livox 激光雷达查看器启动失败')
            self.btn_lidar.setEnabled(True)
            self.btn_lidar_stop.setEnabled(False)
        else:
            self._log_ok(f'Livox 激光雷达查看器已启动 (PID: {proc.processId()})')
            self._proc_lidar = proc

    def _on_stop_lidar_viewer(self):
        if self._proc_lidar is None or self._proc_lidar.state() == QProcess.NotRunning:
            self._log('Livox 激光雷达查看器未在运行')
            return

        self._log('正在停止 Livox 激光雷达查看器...')
        # 向整个进程组发 SIGINT（让 ros2 launch 优雅退出）
        pid = self._proc_lidar.processId()
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGINT)
            except (ProcessLookupError, PermissionError):
                self._proc_lidar.terminate()
        else:
            self._proc_lidar.terminate()

        # 如果 3 秒内没退出就 kill
        QTimer.singleShot(3000, lambda: self._force_kill_lidar())

    def _force_kill_lidar(self):
        if self._proc_lidar and self._proc_lidar.state() != QProcess.NotRunning:
            self._log('Livox 激光雷达查看器未响应 SIGINT，强制终止')
            self._proc_lidar.kill()

    def _on_lidar_finished(self, code, status):
        if code == 0:
            self._log('Livox 激光雷达查看器已退出')
        else:
            self._log_err(f'Livox 激光雷达查看器退出 (code={code})')
        self.btn_lidar.setEnabled(True)
        self.btn_lidar_stop.setEnabled(False)
        self._proc_lidar = None

    # ── QProcess 辅助 ────────────────────────────────────────────
    def _launch_process(self, cmd: str, dot: StatusDot, btn_start: QPushButton,
                        btn_stop: QPushButton, label: str) -> QProcess:
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)

        proc.readyReadStandardOutput.connect(
            lambda: self._on_proc_output(proc)
        )
        proc.finished.connect(
            lambda code, status: self._on_proc_finished(code, status, dot, btn_start, btn_stop, label)
        )

        dot.set_state('running')
        btn_start.setEnabled(False)
        btn_stop.setEnabled(True)

        proc.start('setsid', ['--wait', 'bash', '-c', cmd])
        if not proc.waitForStarted(5000):
            self._log_err(f'{label} 启动失败')
            dot.set_state('error')
            btn_start.setEnabled(True)
            btn_stop.setEnabled(False)
        else:
            self._log_ok(f'{label} 已启动 (PID: {proc.processId()})')
        return proc

    def _on_proc_output(self, proc: QProcess):
        data = proc.readAllStandardOutput().data()
        try:
            text = data.decode('utf-8', errors='replace').rstrip()
        except Exception:
            text = str(data)
        if text:
            self._append_log(text)

    def _on_proc_finished(self, code, status, dot, btn_start, btn_stop, label):
        if label == '整机控制':
            self._auto_start_vr_pending = False
            self._stop_battery_monitor()
        if code == 0:
            self._log(f'{label} 已正常退出')
            dot.set_state('idle')
        else:
            self._log_err(f'{label} 退出 (code={code})')
            dot.set_state('error')
        btn_start.setEnabled(True)
        btn_stop.setEnabled(False)
        self._update_motor_page_lock()

    def _stop_process(self, proc: QProcess | None, dot: StatusDot,
                      btn_start: QPushButton, btn_stop: QPushButton, label: str):
        if proc is None or proc.state() == QProcess.NotRunning:
            self._log(f'{label} 未在运行')
            return
        self._log(f'正在停止 {label}...')
        # 向整个进程组发 SIGINT（让 ros2 launch 优雅退出）
        pid = proc.processId()
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGINT)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
        else:
            proc.terminate()

        # 如果 3 秒内没退出就 kill
        QTimer.singleShot(3000, lambda: self._force_kill(proc, label))

    def _force_kill(self, proc: QProcess, label: str):
        if proc.state() != QProcess.NotRunning:
            self._log(f'{label} 未响应 SIGINT，强制终止')
            proc.kill()

    # ── 窗口关闭 ─────────────────────────────────────────────────
    def closeEvent(self, event):
        # 关窗前先同步失能底盘（controller_manager 还活着）
        if self._proc_bringup and self._proc_bringup.state() != QProcess.NotRunning:
            cmd = (
                f'source {_SETUP_BASH} && '
                'ros2 control set_controller_state swerve_drive_controller inactive && '
                'ros2 control set_hardware_component_state swerve_drive_system inactive'
            )
            try:
                subprocess.run(['bash', '-c', cmd], timeout=8,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

        processes = [
            self._proc_battery,
            self._proc_bringup,
            self._proc_vr,
            self._proc_lidar,
        ]
        for proc in processes:
            if proc and proc.state() != QProcess.NotRunning:
                pid = proc.processId()
                if pid:
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGINT)
                    except Exception:
                        proc.kill()
                else:
                    proc.kill()
                proc.waitForFinished(3000)

        for proc, process_group in (
            (self._proc_camera_ros, True),
            (self._proc_camera, False),
        ):
            if proc and proc.state() != QProcess.NotRunning:
                self._terminate_camera_process(proc, process_group)
                if not proc.waitForFinished(3000):
                    self._force_stop_camera_process(proc, process_group)
        if self.motor_manager_adapter is not None:
            self.motor_manager_adapter.shutdown()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
