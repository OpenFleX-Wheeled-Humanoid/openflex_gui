from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import signal
import tempfile

from PySide6.QtCore import QProcess, QProcessEnvironment, QObject, QTimer, Signal


@dataclass(frozen=True)
class DeploymentTask:
    task_id: str
    label: str
    script_flag: str
    risk: str
    terminal_required: bool
    supports_jobs: bool = False


DEPLOYMENT_TASKS = {
    task.task_id: task
    for task in (
        DeploymentTask(
            "openflex", "完整安装", "--openflex", "安装依赖、驱动并编译工作空间", True, True
        ),
        DeploymentTask(
            "environment", "环境安装", "--environment", "修改系统软件包和 Python 环境", True
        ),
        DeploymentTask(
            "compile", "工作空间编译", "--compile", "重新编译 OpenFlex ROS 2 软件包", False, True
        ),
        DeploymentTask(
            "kcan", "KCAN 驱动", "--kcan", "编译并安装内核驱动", True
        ),
        DeploymentTask(
            "vr", "VR 软件", "--vr", "安装 ADB 并向头显安装软件", True
        ),
        DeploymentTask(
            "camera", "相机配置", "--camera", "交互式选择并写入相机序列号", True
        ),
        DeploymentTask(
            "lidar", "雷达配置", "--lidar", "交互式修改 MID360 网络配置", True
        ),
        DeploymentTask(
            "sync-source", "源码同步", "--sync-source", "下载或更新发售版源码", True
        ),
    )
}


class DeploymentCommandBuilder:
    def __init__(self, script_path: str | Path, workspace_dir: str | Path):
        self.script_path = Path(script_path).expanduser().resolve()
        self.workspace_dir = Path(workspace_dir).expanduser().resolve()
        if not self.script_path.is_file():
            raise FileNotFoundError(f"deployment script not found: {self.script_path}")
        if not self.workspace_dir.is_dir():
            raise FileNotFoundError(f"workspace not found: {self.workspace_dir}")

    @staticmethod
    def task(task_id: str) -> DeploymentTask:
        try:
            return DEPLOYMENT_TASKS[task_id]
        except KeyError as exc:
            raise ValueError(f"unsupported deployment task: {task_id}") from exc

    @staticmethod
    def _validate_jobs(jobs: int) -> None:
        if isinstance(jobs, bool) or not isinstance(jobs, int) or not 1 <= jobs <= 32:
            raise ValueError("parallel workers must be an integer from 1 to 32")

    def build(self, task_id: str, *, dry_run: bool = False, jobs: int = 1) -> list[str]:
        task = self.task(task_id)
        self._validate_jobs(jobs)
        command = [str(self.script_path), task.script_flag]
        if dry_run:
            command.append("--dry-run")
        if task.supports_jobs:
            command.extend(("--jobs", str(jobs)))
        return command

    def requires_terminal(self, task_id: str, *, dry_run: bool = False) -> bool:
        task = self.task(task_id)
        return task.terminal_required and not dry_run


class DeploymentRunner(QObject):
    output = Signal(str)
    state_changed = Signal(str)
    finished = Signal(str, int, bool)

    def __init__(self, builder: DeploymentCommandBuilder, process_factory=None):
        super().__init__()
        self.builder = builder
        self._process_factory = process_factory or QProcess
        self._process = None
        self._active_task = None
        self._temporary_dir = None
        self._log_offset = 0
        self._cancel_requested = False
        self._cancel_process_group = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(150)
        self._poll_timer.timeout.connect(self._poll_terminal_log)

    @property
    def is_running(self) -> bool:
        return self._process is not None

    @property
    def active_task(self) -> str | None:
        return self._active_task

    def start(self, task_id: str, *, dry_run: bool = False, jobs: int = 1) -> None:
        if self.is_running:
            raise RuntimeError("a deployment task is already running")

        command = self.builder.build(task_id, dry_run=dry_run, jobs=jobs)
        self._active_task = task_id
        self._log_offset = 0
        self._temporary_dir = None
        self._cancel_requested = False
        self._cancel_process_group = None
        self._process = self._process_factory()
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read_direct_output)
        self._process.finished.connect(self._on_process_finished)
        if hasattr(self._process, "errorOccurred"):
            self._process.errorOccurred.connect(self._on_process_error)

        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("OPENFLEX_WORKSPACE", str(self.builder.workspace_dir))
        self._process.setProcessEnvironment(environment)
        self._process.setWorkingDirectory(str(self.builder.workspace_dir))

        if self.builder.requires_terminal(task_id, dry_run=dry_run):
            self._start_terminal(command, environment)
        else:
            self._start_direct(command)
        self.state_changed.emit("running")

    def _start_direct(self, command: list[str]) -> None:
        self._process.setProgram("/usr/bin/setsid")
        self._process.setArguments(command)
        self._process.start()

    def _start_terminal(self, command: list[str], environment: QProcessEnvironment) -> None:
        self._temporary_dir = Path(tempfile.mkdtemp(prefix="openflex-deploy-"))
        bridge = Path(__file__).with_name("deployment_terminal_bridge.py")
        arguments = [
            "--wait",
            "--",
            "/usr/bin/python3",
            str(bridge),
            "--state-dir",
            str(self._temporary_dir),
            "--installer-args",
            *command,
        ]
        self._process.setProgram("/usr/bin/gnome-terminal")
        self._process.setArguments(arguments)
        self._process.start()
        self._poll_timer.start()

    def _read_direct_output(self) -> None:
        data = self._process.readAllStandardOutput()
        if hasattr(data, "data"):
            data = data.data()
        text = bytes(data).decode(errors="replace")
        if text:
            self.output.emit(text)

    def _poll_terminal_log(self) -> None:
        if not self._temporary_dir:
            return
        log_path = self._temporary_dir / "output.log"
        if not log_path.exists():
            return
        with log_path.open("rb") as log_file:
            log_file.seek(self._log_offset)
            data = log_file.read()
            self._log_offset = log_file.tell()
        if data:
            self.output.emit(data.decode(errors="replace"))

    def _on_process_error(self, error) -> None:
        if self._process is None:
            return
        message = self._process.errorString()
        if message:
            self.output.emit(f"[部署进程错误] {message}\n")
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(-1)

    def _on_process_finished(self, exit_code: int, exit_status) -> None:
        self._complete(exit_code)

    def _complete(self, exit_code: int) -> None:
        if self._process is None:
            return
        self._poll_terminal_log()
        task_id = self._active_task
        if self._temporary_dir:
            status_path = self._temporary_dir / "exit.status"
            if status_path.exists():
                try:
                    exit_code = int(status_path.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    pass
        self._poll_timer.stop()
        self._process = None
        self._active_task = None
        if self._temporary_dir:
            shutil.rmtree(self._temporary_dir, ignore_errors=True)
            self._temporary_dir = None
        cancelled = self._cancel_requested
        self._cancel_requested = False
        self._cancel_process_group = None
        success = exit_code == 0 and not cancelled
        state = "cancelled" if cancelled else "success" if success else "failed"
        self.state_changed.emit(state)
        self.finished.emit(task_id, int(exit_code), success)

    def cancel(self) -> None:
        process = self._process
        if process is None:
            return
        self._cancel_requested = True
        process_group = None
        if self._temporary_dir:
            pid_path = self._temporary_dir / "process.pid"
            if pid_path.exists():
                try:
                    candidate = int(pid_path.read_text(encoding="ascii").strip())
                    if candidate > 1 and candidate != os.getpgrp():
                        process_group = candidate
                except (OSError, ValueError):
                    pass
        elif process.processId():
            process_group = int(process.processId())
        self._cancel_process_group = process_group
        try:
            if process_group:
                os.killpg(process_group, signal.SIGINT)
            else:
                process.terminate()
        except (ProcessLookupError, PermissionError, OSError):
            process.terminate()
        QTimer.singleShot(3000, lambda: self._force_cancel(process))

    def _force_cancel(self, process) -> None:
        if process is None or self._process is not process:
            return
        state = process.state() if hasattr(process, "state") else QProcess.ProcessState.Running
        if state != QProcess.ProcessState.NotRunning:
            if self._cancel_process_group:
                try:
                    os.killpg(self._cancel_process_group, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            process.kill()
