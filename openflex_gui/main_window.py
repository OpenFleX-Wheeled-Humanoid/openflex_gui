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
import shutil

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTextEdit, QLabel, QGroupBox, QSizePolicy, QFrame, QCheckBox
)
from PyQt5.QtCore import Qt, QProcess, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont, QColor, QTextCursor

# ─── 项目路径 ──────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


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
_TERMINAL_CANDIDATES = ('gnome-terminal', 'terminator', 'xterm')

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
    log = pyqtSignal(str)
    log_html = pyqtSignal(str)
    ui = pyqtSignal(object)


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
        self.set_state('idle')

    def set_state(self, state: str):
        c = self._COLORS.get(state, self._COLORS['idle'])
        self.setStyleSheet(
            f"background-color: {c}; border-radius: 8px; border: 1px solid #555;"
        )


# ─── 主窗口 ──────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('OpenFlex VR 全身控制上位机')
        self.setMinimumSize(800, 600)
        self._signals = _Signals()
        self._signals.log.connect(self._append_log)
        self._signals.log_html.connect(self._append_log_html)
        self._signals.ui.connect(self._run_ui_callback)

        # 子进程管理
        self._proc_bringup: QProcess | None = None
        self._proc_vr: QProcess | None = None
        self._proc_keyboard: QProcess | None = None
        self._proc_battery: QProcess | None = None
        self._start_sequence_after_can = False
        self._auto_start_vr_pending = False
        self._auto_start_vr_attempts = 0
        self._auto_start_vr_max_attempts = 20

        self._build_ui()

    # ── UI 构建 ──────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(12)

        title = QLabel('OpenFlex VR 全身控制')
        title.setFont(QFont('Sans', 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # --- 1. CAN 总线 ---
        grp_can = QGroupBox('1. CAN 总线')
        h1 = QHBoxLayout(grp_can)
        self.dot_can = StatusDot()
        self.btn_enable_can = QPushButton('启用全部 CAN')
        self.btn_enable_can.setMinimumHeight(40)
        self.btn_enable_can.clicked.connect(self._on_enable_can)
        self.btn_disable_can = QPushButton('禁用全部 CAN')
        self.btn_disable_can.setMinimumHeight(40)
        self.btn_disable_can.clicked.connect(self._on_disable_can)
        h1.addWidget(self.dot_can)
        h1.addWidget(self.btn_enable_can, 1)
        h1.addWidget(self.btn_disable_can, 1)
        root.addWidget(grp_can)

        # --- 2. 电机状态 ---
        grp_status = QGroupBox('2. 电机状态')
        h2 = QHBoxLayout(grp_status)
        self.dot_status = StatusDot()
        self.btn_status = QPushButton('检查全部电机状态')
        self.btn_status.setMinimumHeight(40)
        self.btn_status.clicked.connect(self._on_check_status)
        h2.addWidget(self.dot_status)
        h2.addWidget(self.btn_status, 1)
        root.addWidget(grp_status)

        # --- 3. 整机控制 ---
        grp_bringup = QGroupBox('3. 整机控制 (ros2 launch)')
        h3 = QHBoxLayout(grp_bringup)
        self.dot_bringup = StatusDot()
        self.btn_bringup_start = QPushButton('启动整机控制')
        self.btn_bringup_start.setMinimumHeight(40)
        self.btn_bringup_start.clicked.connect(self._on_start_bringup)
        self.btn_bringup_stop = QPushButton('停止')
        self.btn_bringup_stop.setMinimumHeight(40)
        self.btn_bringup_stop.setFixedWidth(80)
        self.btn_bringup_stop.setEnabled(False)
        self.btn_bringup_stop.clicked.connect(self._on_stop_bringup)
        h3.addWidget(self.dot_bringup)
        h3.addWidget(self.btn_bringup_start, 1)
        h3.addWidget(self.btn_bringup_stop)
        root.addWidget(grp_bringup)

        # --- 4. VR 遥操作 ---
        grp_vr = QGroupBox('4. VR 遥操作 (ros2 launch)')
        h4 = QHBoxLayout(grp_vr)
        self.dot_vr = StatusDot()
        self.btn_vr_start = QPushButton('启动 VR 遥操作')
        self.btn_vr_start.setMinimumHeight(40)
        self.btn_vr_start.clicked.connect(self._on_start_vr)
        self.chk_vr_chassis = QCheckBox('vr控制底盘速度')
        self.chk_vr_chassis.setToolTip('勾选后使用 VR 发来的底盘线速度/角速度上限')
        self.btn_vr_stop = QPushButton('停止')
        self.btn_vr_stop.setMinimumHeight(40)
        self.btn_vr_stop.setFixedWidth(80)
        self.btn_vr_stop.setEnabled(False)
        self.btn_vr_stop.clicked.connect(self._on_stop_vr)
        h4.addWidget(self.dot_vr)
        h4.addWidget(self.btn_vr_start, 1)
        h4.addWidget(self.chk_vr_chassis)
        h4.addWidget(self.btn_vr_stop)
        root.addWidget(grp_vr)

        # --- 5. 键盘底盘控制 ---
        grp_keyboard = QGroupBox('5. 键盘底盘控制 (ros2 run)')
        h5 = QHBoxLayout(grp_keyboard)
        self.dot_keyboard = StatusDot()
        self.btn_keyboard_start = QPushButton('启动键盘控制底盘')
        self.btn_keyboard_start.setMinimumHeight(40)
        self.btn_keyboard_start.clicked.connect(self._on_start_keyboard_teleop)
        self.btn_keyboard_stop = QPushButton('停止')
        self.btn_keyboard_stop.setMinimumHeight(40)
        self.btn_keyboard_stop.setFixedWidth(80)
        self.btn_keyboard_stop.setEnabled(False)
        self.btn_keyboard_stop.clicked.connect(self._on_stop_keyboard_teleop)
        h5.addWidget(self.dot_keyboard)
        h5.addWidget(self.btn_keyboard_start, 1)
        h5.addWidget(self.btn_keyboard_stop)
        root.addWidget(grp_keyboard)

        # --- 6. 电量显示 ---
        grp_battery = QGroupBox('6. 电量显示 (ros2 launch)')
        h5 = QHBoxLayout(grp_battery)
        self.dot_battery = StatusDot()
        self.btn_battery_start = QPushButton('显示电量')
        self.btn_battery_start.setMinimumHeight(40)
        self.btn_battery_start.clicked.connect(self._on_start_battery)
        self.btn_battery_stop = QPushButton('停止')
        self.btn_battery_stop.setMinimumHeight(40)
        self.btn_battery_stop.setFixedWidth(80)
        self.btn_battery_stop.setEnabled(False)
        self.btn_battery_stop.clicked.connect(self._on_stop_battery)
        h5.addWidget(self.dot_battery)
        h5.addWidget(self.btn_battery_start, 1)
        h5.addWidget(self.btn_battery_stop)
        root.addWidget(grp_battery)

        # --- 日志 ---
        log_label = QLabel('日志输出:')
        log_label.setFont(QFont('Sans', 10, QFont.Bold))
        root.addWidget(log_label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont('Monospace', 9))
        self.log_view.setTextColor(QColor('#2ecc71'))
        self.log_view.setMinimumHeight(180)
        root.addWidget(self.log_view, 1)

        # 清空日志按钮
        btn_clear = QPushButton('清空日志')
        btn_clear.clicked.connect(self.log_view.clear)
        root.addWidget(btn_clear)

        self._refresh_can_ui_state()

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
        try:
            import can_utils as cu
        except ImportError as e:
            self._log_err(f'无法导入 can_utils: {e}')
            self._call_ui(lambda: self.dot_status.set_state('idle'))
            self._call_ui(lambda: self.btn_status.setEnabled(True))
            return

        all_ok = True

        # --- 升降台 ---
        self._log('\n【升降台】')
        try:
            if self._iface_up('can3'):
                sock = cu.open_can_socket('can3', recv_timeout_s=0.3)
                ok, sw, state_str = cu.lift_get_status(sock, 16)
                sock.close()
                self._log('  CAN: can3  node_id=16')
                if ok:
                    self._log(f'    状态: {state_str}  (statusword=0x{sw:04X})')
                    self._log('  🔴响应[正常]')
                else:
                    self._log_err('  无响应')
                    all_ok = False
            else:
                self._log(f'  can3 不可用')
                all_ok = False
        except Exception as e:
            self._log_err(f'  升降台查询异常: {e}')
            all_ok = False

        # --- RS06 转向 ---
        self._log('\n【底盘转向 RS06】')
        try:
            if self._iface_up('can5'):
                sock = cu.open_can_socket('can5', recv_timeout_s=0.1)
                self._log(f'  CAN: can5  电机 id={cu.RS06_MOTOR_IDS}')
                self._log('  ID    状态       位置(rad)      速度(r/s)      扭矩(Nm)       温度(°C)')
                self._log('  ------------------------------------------------------------')
                for mid in cu.RS06_MOTOR_IDS:
                    ok, pos, vel, trq, tmp = cu.rs06_get_status(sock, mid)
                    if ok:
                        self._log(f'  {mid:<5} 🔴响应       {pos:<12.3f} {vel:<12.3f} '
                                  f'{trq:<12.3f} {tmp:<10.1f}')
                    else:
                        self._log_err(f'  {mid:<5} 无响应       {"-":<12} {"-":<12} '
                                      f'{"-":<12} {"-":<10}')
                        all_ok = False
                sock.close()
            else:
                self._log(f'  can5 不可用')
                all_ok = False
        except Exception as e:
            self._log_err(f'  RS06 查询异常: {e}')
            all_ok = False

        # --- UM 轮毂电机 ---
        self._log('\n【底盘驱动 UM 轮毂电机】')
        try:
            if self._iface_up('can4'):
                sock = cu.open_can_socket('can4', recv_timeout_s=0.3)
                self._log(f'  CAN: can4  node_id={cu.UM_NODE_IDS}')
                self._log('  Node   状态                                      StatusWord')
                self._log('  -------------------------------------------------------')
                for nid in cu.UM_NODE_IDS:
                    ok, sw, state_str = cu.um_get_status(sock, nid)
                    if ok:
                        self._log(f'  {nid:<6} {state_str}🔴响应[正常]              0x{sw:04X}')
                    else:
                        self._log_err(f'  {nid:<6} 无响应                                    -')
                        all_ok = False
                sock.close()
            else:
                self._log(f'  can4 不可用')
                all_ok = False
        except Exception as e:
            self._log_err(f'  UM 查询异常: {e}')
            all_ok = False

        # --- 双臂 ---
        self._log('\n【双臂】')
        try:
            from openarmx_arm_driver import Robot
            robot = Robot(
                right_can_channel='can0', left_can_channel='can1',
                auto_enable_can=False,
            )
            # capture show_all_status output
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                robot.show_all_status()
            self._log(buf.getvalue().rstrip())
            robot.shutdown()
        except ImportError:
            self._log('  openarmx_arm_driver 未安装，跳过双臂')
        except Exception as e:
            self._log_err(f'  双臂查询异常: {e}')
            all_ok = False

        # --- 头部 ---
        self._log('\n【头部】')
        try:
            from openarmx_arm_driver import Arm
            arm = Arm(
                can_channel='can2', side='right',
                motor_ids=[1, 2], auto_enable_can=False,
            )
            import io, contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                arm.show_motor_status()
            self._log(buf.getvalue().rstrip())
            arm.close()
        except ImportError:
            self._log('  openarmx_arm_driver 未安装，跳过头部')
        except Exception as e:
            self._log_err(f'  头部查询异常: {e}')
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

    # ── 3. 整机控制 ──────────────────────────────────────────────
    def _on_start_bringup(self):
        if self._proc_bringup and self._proc_bringup.state() != QProcess.NotRunning:
            self._log('整机控制已在运行中')
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
        self._stop_process(self._proc_bringup, self.dot_bringup,
                           self.btn_bringup_start, self.btn_bringup_stop, '整机控制')

    # ── 4. VR 遥操作 ─────────────────────────────────────────────
    def _on_start_vr(self):
        self._auto_start_vr_pending = False
        if self._proc_vr and self._proc_vr.state() != QProcess.NotRunning:
            self._log('VR 遥操作已在运行中')
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

    def _on_stop_vr(self):
        self._auto_start_vr_pending = False
        self._stop_process(self._proc_vr, self.dot_vr,
                           self.btn_vr_start, self.btn_vr_stop, 'VR 遥操作')

    # ── 5. 键盘底盘控制 ────────────────────────────────────────
    def _detect_terminal(self) -> str | None:
        for terminal in _TERMINAL_CANDIDATES:
            if shutil.which(terminal):
                return terminal
        return None

    def _on_start_keyboard_teleop(self):
        if self._proc_keyboard and self._proc_keyboard.state() != QProcess.NotRunning:
            self._log('键盘底盘控制已在运行中')
            return

        terminal = self._detect_terminal()
        if terminal is None:
            self._log_err('未找到可用终端程序，无法启动键盘底盘控制')
            self.dot_keyboard.set_state('error')
            return

        self._log('=' * 50)
        self._log('启动键盘底盘控制...')
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 run swerve_bringup swerve_teleop.py'
        )
        self._proc_keyboard = self._launch_terminal_process(
            terminal, cmd, self.dot_keyboard,
            self.btn_keyboard_start, self.btn_keyboard_stop, '键盘底盘控制'
        )

    def _on_stop_keyboard_teleop(self):
        self._stop_process(self._proc_keyboard, self.dot_keyboard,
                           self.btn_keyboard_start, self.btn_keyboard_stop, '键盘底盘控制')

    # ── 6. 电量显示 ─────────────────────────────────────────────
    def _on_start_battery(self):
        if self._proc_battery and self._proc_battery.state() != QProcess.NotRunning:
            self._log('电量显示已在运行中')
            return

        self._log('=' * 50)
        self._log('准备显示电量...')
        self.btn_battery_start.setEnabled(False)
        self.btn_battery_stop.setEnabled(False)
        self.dot_battery.set_state('running')
        threading.Thread(target=self._battery_permission_then_launch, daemon=True).start()

    def _battery_permission_then_launch(self):
        if not os.path.exists(_BATTERY_SERIAL_HELPER):
            self._log_err(f'电池串口 helper 不存在: {_BATTERY_SERIAL_HELPER}')
            self._call_ui(lambda: self.dot_battery.set_state('error'))
            self._call_ui(lambda: self.btn_battery_start.setEnabled(True))
            return

        try:
            ret = subprocess.run(
                ['bash', _BATTERY_SERIAL_HELPER],
                capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:
            self._log_err('电池串口 helper 执行超时')
            self._call_ui(lambda: self.dot_battery.set_state('error'))
            self._call_ui(lambda: self.btn_battery_start.setEnabled(True))
            return
        except Exception as e:
            self._log_err(f'执行电池串口 helper 异常: {e}')
            self._call_ui(lambda: self.dot_battery.set_state('error'))
            self._call_ui(lambda: self.btn_battery_start.setEnabled(True))
            return

        output = (ret.stdout or '').strip()
        err_output = (ret.stderr or '').strip()
        if output:
            for line in output.splitlines():
                self._log(line)
        if err_output:
            for line in err_output.splitlines():
                self._log_err(line)

        if ret.returncode != 0:
            self._log_err(f'电池串口 helper 退出失败 (code={ret.returncode})')
            self._call_ui(lambda: self.dot_battery.set_state('error'))
            self._call_ui(lambda: self.btn_battery_start.setEnabled(True))
            return

        self._call_ui(self._launch_battery_monitor)

    def _launch_battery_monitor(self):
        self._log('启动电量显示...')
        cmd = (
            f'source {_SETUP_BASH} && '
            'ros2 launch openarmx_battery_monitor auto_pack_overlay.launch.py '
            'start_rviz:=false'
        )
        self._proc_battery = self._launch_process(
            cmd, self.dot_battery, self.btn_battery_start, self.btn_battery_stop, '电量显示'
        )

    def _on_stop_battery(self):
        self._stop_process(self._proc_battery, self.dot_battery,
                           self.btn_battery_start, self.btn_battery_stop, '电量显示')

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

    def _launch_terminal_process(self, terminal: str, cmd: str, dot: StatusDot,
                                 btn_start: QPushButton, btn_stop: QPushButton,
                                 label: str) -> QProcess:
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

        if terminal == 'gnome-terminal':
            args = ['--wait', '--title=OpenFlex 键盘底盘控制', '--', 'bash', '-lc', cmd]
        elif terminal == 'terminator':
            args = ['-T', 'OpenFlex 键盘底盘控制', '-x', 'bash', '-lc', cmd]
        else:
            args = ['-T', 'OpenFlex 键盘底盘控制', '-e', f'bash -lc "{cmd}"']

        proc.start('setsid', ['--wait', terminal, *args])
        if not proc.waitForStarted(5000):
            self._log_err(f'{label} 启动失败')
            dot.set_state('error')
            btn_start.setEnabled(True)
            btn_stop.setEnabled(False)
        else:
            self._log_ok(f'{label} 已启动，请在弹出的终端窗口中按键控制底盘')
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
        if code == 0:
            self._log(f'{label} 已正常退出')
            dot.set_state('idle')
        else:
            self._log_err(f'{label} 退出 (code={code})')
            dot.set_state('error')
        btn_start.setEnabled(True)
        btn_stop.setEnabled(False)

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

        for proc in [self._proc_bringup, self._proc_vr, self._proc_keyboard, self._proc_battery]:
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
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
