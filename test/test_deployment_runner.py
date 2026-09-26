from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication


class FakeSignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeProcess:
    def __init__(self):
        self.readyReadStandardOutput = FakeSignal()
        self.finished = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.program = None
        self.arguments = None
        self.working_directory = None
        self.environment = None
        self.started = False
        self.terminated = False
        self.killed = False
        self.writes = []

    def setProcessChannelMode(self, mode):
        self.channel_mode = mode

    def setWorkingDirectory(self, path):
        self.working_directory = path

    def setProcessEnvironment(self, environment):
        self.environment = environment

    def setProgram(self, program):
        self.program = program

    def setArguments(self, arguments):
        self.arguments = list(arguments)

    def start(self):
        self.started = True

    def processId(self):
        return 4242

    def state(self):
        return QProcess.Running if self.started else QProcess.NotRunning

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def errorString(self):
        return ""

    def readAllStandardOutput(self):
        return b""

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


class DeploymentRunnerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.workspace = root / "openflex_ws"
        self.workspace.mkdir()
        self.script = root / "install_openflex_drivers_and_build.sh"
        self.script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        self.processes = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def _process_factory(self):
        process = FakeProcess()
        self.processes.append(process)
        return process

    def _runner(self):
        from openflex_gui.deployment_runner import (
            DeploymentCommandBuilder,
            DeploymentRunner,
        )

        builder = DeploymentCommandBuilder(self.script, self.workspace)
        return DeploymentRunner(builder, process_factory=self._process_factory)

    def test_compile_uses_direct_process_group_and_workspace_environment(self):
        runner = self._runner()

        runner.start("compile", jobs=3)

        process = self.processes[-1]
        self.assertEqual(process.program, "/usr/bin/setsid")
        self.assertEqual(
            process.arguments,
            [str(self.script), "--compile", "--jobs", "3"],
        )
        self.assertEqual(process.working_directory, str(self.workspace))
        self.assertEqual(
            process.environment.value("OPENFLEX_WORKSPACE"), str(self.workspace)
        )
        self.assertTrue(runner.is_running)

        process.finished.emit(0, QProcess.NormalExit)
        self.assertFalse(runner.is_running)

    def test_interactive_driver_install_uses_embedded_pty_bridge(self):
        runner = self._runner()

        runner.start("kcan")

        process = self.processes[-1]
        self.assertEqual(process.program, "/usr/bin/python3")
        self.assertIn("deployment_terminal_bridge.py", process.arguments[0])
        separator = process.arguments.index("--installer-args")
        self.assertEqual(
            process.arguments[separator + 1 :],
            [str(self.script), "--kcan"],
        )
        runner.send_input("secret")
        self.assertEqual(process.writes, [b"secret\n"])
        self.assertTrue(runner.is_running)

        process.finished.emit(0, QProcess.NormalExit)
        self.assertFalse(runner.is_running)

    def test_interactive_task_does_not_read_bridge_stdout_as_a_second_log(self):
        runner = self._runner()

        runner.start("kcan")

        self.assertEqual(self.processes[-1].readyReadStandardOutput.callbacks, [])

        self.processes[-1].finished.emit(0, QProcess.NormalExit)

    def test_start_writes_ordered_interactive_input_lines_once(self):
        runner = self._runner()

        runner.start("vr", input_lines=["2", "yes"])

        process = self.processes[-1]
        self.assertEqual(process.writes, [b"2\n", b"yes\n"])

        process.finished.emit(0, QProcess.NormalExit)

    def test_direct_task_rejects_interactive_input(self):
        runner = self._runner()

        with self.assertRaises(ValueError):
            runner.start("compile", input_lines=["unexpected"])

        self.assertFalse(runner.is_running)

    def test_rejects_a_second_task_while_running(self):
        runner = self._runner()
        runner.start("compile")

        with self.assertRaises(RuntimeError):
            runner.start("kcan", dry_run=True)

        self.processes[-1].finished.emit(0, QProcess.NormalExit)

    def test_terminal_cancel_targets_installer_process_group(self):
        runner = self._runner()
        states = []
        runner.state_changed.connect(states.append)
        runner.start("kcan")
        (runner._temporary_dir / "process.pid").write_text("777\n", encoding="ascii")

        with patch("openflex_gui.deployment_runner.os.killpg") as killpg:
            runner.cancel()

        killpg.assert_called_once_with(777, 2)
        self.processes[-1].finished.emit(130, QProcess.NormalExit)
        self.assertEqual(states[-1], "cancelled")

    def test_force_cancel_terminates_installer_process_group(self):
        runner = self._runner()
        runner.start("kcan")
        process = self.processes[-1]
        runner._cancel_process_group = 777

        with patch("openflex_gui.deployment_runner.os.killpg") as killpg:
            runner._force_cancel(process)

        killpg.assert_called_once_with(777, signal.SIGKILL)
        self.assertFalse(process.killed)
        process.finished.emit(137, QProcess.NormalExit)

    def test_failed_process_start_resets_runner(self):
        runner = self._runner()
        completions = []
        runner.finished.connect(lambda task, code, success: completions.append((task, code, success)))
        runner.start("compile", dry_run=True)

        self.processes[-1].errorOccurred.emit(QProcess.FailedToStart)

        self.assertFalse(runner.is_running)
        self.assertEqual(completions, [("compile", -1, False)])

    def test_terminal_completion_uses_bridge_exit_status(self):
        runner = self._runner()
        completions = []
        runner.finished.connect(lambda task, code, success: completions.append((task, code, success)))
        runner.start("kcan")
        (runner._temporary_dir / "exit.status").write_text("7\n", encoding="ascii")

        self.processes[-1].finished.emit(0, QProcess.NormalExit)

        self.assertEqual(completions, [("kcan", 7, False)])

    def test_terminal_bridge_mirrors_output_and_preserves_exit_code(self):
        state_dir = Path(self.temp_dir.name) / "state"
        command = Path(self.temp_dir.name) / "interactive-task.sh"
        command.write_text(
            "#!/usr/bin/env bash\nprintf 'bridge-output\\n'\nexit 7\n",
            encoding="utf-8",
        )
        command.chmod(0o755)
        bridge = (
            Path(__file__).resolve().parents[1]
            / "openflex_gui"
            / "deployment_terminal_bridge.py"
        )
        environment = os.environ.copy()
        environment["OPENFLEX_WORKSPACE"] = str(self.workspace)

        result = subprocess.run(
            [
                sys.executable,
                str(bridge),
                "--state-dir",
                str(state_dir),
                "--installer-args",
                str(command),
            ],
            capture_output=True,
            text=True,
            env=environment,
            timeout=5,
        )

        self.assertEqual(result.returncode, 7)
        self.assertNotIn("bridge-output", result.stdout)
        self.assertIn("bridge-output", (state_dir / "output.log").read_text())
        self.assertEqual((state_dir / "exit.status").read_text().strip(), "7")

    def test_terminal_bridge_accepts_input_without_external_terminal(self):
        state_dir = Path(self.temp_dir.name) / "interactive-state"
        command = Path(self.temp_dir.name) / "interactive-task.sh"
        command.write_text(
            "#!/usr/bin/env bash\nread -r value\nprintf 'received=%s\\n' \"$value\"\n",
            encoding="utf-8",
        )
        command.chmod(0o755)
        bridge = Path(__file__).resolve().parents[1] / "openflex_gui" / "deployment_terminal_bridge.py"
        process = subprocess.Popen(
            [sys.executable, str(bridge), "--state-dir", str(state_dir), "--installer-args", str(command)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        process.stdin.write("embedded-input\n")
        process.stdin.flush()
        output, _ = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertNotIn("received=embedded-input", output)
        self.assertIn(
            "received=embedded-input",
            (state_dir / "output.log").read_text(encoding="utf-8"),
        )


class DeploymentCommandBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.workspace = root / "openflex_ws"
        self.workspace.mkdir()
        self.script = root / "install_openflex_drivers_and_build.sh"
        self.script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _module(self):
        from openflex_gui import deployment_runner

        return deployment_runner

    def test_catalog_contains_only_supported_non_desktop_tasks(self):
        module = self._module()

        self.assertEqual(
            list(module.DEPLOYMENT_TASKS),
            [
                "environment",
                "compile",
                "kcan",
                "vr",
                "camera",
                "lidar",
                "sync-source",
            ],
        )
        self.assertNotIn("desktop", module.DEPLOYMENT_TASKS)

    def test_builds_only_validated_argument_arrays(self):
        module = self._module()
        builder = module.DeploymentCommandBuilder(self.script, self.workspace)

        self.assertEqual(
            builder.build("compile"),
            [str(self.script), "--compile"],
        )
        self.assertEqual(
            builder.build("compile", jobs=4),
            [str(self.script), "--compile", "--jobs", "4"],
        )
        self.assertEqual(
            builder.build("kcan", dry_run=True),
            [str(self.script), "--kcan", "--dry-run"],
        )
        with self.assertRaises(ValueError):
            builder.build("desktop")
        with self.assertRaises(ValueError):
            builder.build("compile; rm -rf /tmp/example")

    def test_validates_parallel_workers(self):
        module = self._module()
        builder = module.DeploymentCommandBuilder(self.script, self.workspace)

        for invalid in (0, 33, True, "4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                builder.build("compile", jobs=invalid)

    def test_dry_run_never_requires_terminal(self):
        module = self._module()
        builder = module.DeploymentCommandBuilder(self.script, self.workspace)

        self.assertFalse(builder.requires_terminal("kcan", dry_run=True))
        self.assertFalse(builder.requires_terminal("compile", dry_run=False))
        self.assertTrue(builder.requires_terminal("kcan", dry_run=False))

    def test_compile_dry_run_does_not_require_driver_archive(self):
        workspace = Path(self.temp_dir.name) / "release" / "openflex_ws"
        (workspace / "src").mkdir(parents=True)
        installer = (
            Path(__file__).resolve().parents[3]
            / "OpenFleX"
            / "install_openflex_drivers_and_build.sh"
        )
        environment = os.environ.copy()
        environment["OPENFLEX_WORKSPACE"] = str(workspace)

        result = subprocess.run(
            [str(installer), "--compile", "--dry-run"],
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
