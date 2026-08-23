# 部署任务专用配置界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the generic deployment input row and collect task-specific parameters through validated PySide6 dialogs while preserving the existing installer script and embedded PTY execution.

**Architecture:** Add a focused `deployment_config_dialog.py` module that returns immutable task configuration data. `MainWindow` opens the appropriate dialog before confirmation and passes the resulting ordered input lines to `DeploymentRunner`; the runner writes those lines once the PTY process starts and keeps its existing log, cancellation, and exit handling.

**Tech Stack:** Python 3, PySide6, Qt widgets/signals, unittest, existing ROS 2 Python package layout.

---

### Task 1: Add task configuration models and dialogs

**Files:**
- Create: `openflex_gui/deployment_config_dialog.py`
- Test: `test/test_deployment_config_dialog.py`

- [ ] **Step 1: Write failing tests for each form result.**

  Add tests that instantiate `DeploymentConfigDialog` with a `QApplication` and assert:

  ```python
  dialog = DeploymentConfigDialog("vr")
  dialog.vr_device.setCurrentText("Quest")
  dialog.vr_connected.setChecked(True)
  assert dialog.configuration() == {
      "input_lines": ["2", "yes"],
  }
  ```

  Cover camera four-field ordering, lidar range validation for `0..255`, source menu mapping, and that a cancelled dialog returns no configuration.

- [ ] **Step 2: Run the focused tests and verify they fail.**

  Run:

  ```bash
  cd /home/openflex/openflex_all_new/Release-version/openflex_ws/src/openflex_integrated/openflex_gui
  QT_QPA_PLATFORM=offscreen pytest -q test/test_deployment_config_dialog.py
  ```

  Expected: collection or attribute failures because the new module does not exist.

- [ ] **Step 3: Implement the dialog module.**

  Define `DeploymentConfigDialog(QDialog)` with task-specific widgets and a `configuration()` method returning `{"input_lines": list[str]}`. Use `QFormLayout`, `QComboBox`, `QLineEdit`, `QCheckBox`, `QDialogButtonBox`, and `QIntValidator(0, 255)` for numeric fields. For non-interactive tasks expose only a confirmation message and return an empty input list. Do not write files or log field values.

- [ ] **Step 4: Run the focused tests and verify they pass.**

  Run the same command; expected result is all dialog tests passing.

### Task 2: Make runner accept ordered task input

**Files:**
- Modify: `openflex_gui/deployment_runner.py`
- Test: `test/test_deployment_runner.py`

- [ ] **Step 1: Add failing runner tests.**

  Extend `FakeProcess` and add a test calling:

  ```python
  runner.start("vr", input_lines=["2", "yes"])
  assert process.writes == [b"2\nyes\n"]
  ```

  Assert direct tasks reject or ignore non-empty input, and an empty list produces no writes.

- [ ] **Step 2: Run the focused runner tests and verify the new tests fail.**

  Run `QT_QPA_PLATFORM=offscreen pytest -q test/test_deployment_runner.py` and confirm the new keyword is unsupported.

- [ ] **Step 3: Implement the minimal runner change.**

  Change `DeploymentRunner.start(..., input_lines: Sequence[str] | None = None)`, store a copy, and add `_write_pending_input()` after `_start_terminal(...)` starts the process. Write each line with exactly one trailing newline, using `send_input`, and clear the pending list so it cannot be replayed. Leave direct process tasks untouched.

- [ ] **Step 4: Run runner tests.**

  Run the focused command and expect all existing and new tests to pass, including PTY exit-code and cancellation tests.

### Task 3: Replace the generic GUI input row with dialog flow

**Files:**
- Modify: `openflex_gui/main_window.py`
- Test: `test/test_main_window_ui.py`

- [ ] **Step 1: Update GUI tests to describe the new contract.**

  Remove assertions for `deployment_input` and `btn_deployment_send`; assert those attributes and object names are absent. Add tests patching `DeploymentConfigDialog.exec` and `configuration` to verify a camera task starts with its ordered `input_lines`, while a rejected dialog leaves `runner.starts` empty.

- [ ] **Step 2: Run the GUI tests and verify they fail.**

  Run `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`; expected failures are the old widgets still being present and `start` receiving no input lines.

- [ ] **Step 3: Implement the GUI changes.**

  Remove the input row creation, `_send_deployment_input`, and send/input state handling. Import the new dialog, open it in `_on_deployment_run` before the existing confirmation, and pass `configuration.get("input_lines", [])` to `deployment_runner.start`. Keep conflict checks and task confirmation unchanged. Make the dialog parent the main window so it follows the active theme.

- [ ] **Step 4: Run GUI tests and an offscreen smoke check.**

  Run the focused test file, then launch a minimal `MainWindow` under `QT_QPA_PLATFORM=offscreen` and assert the deployment page has seven cards, no generic input widgets, and all card buttons remain enabled when idle.

### Task 4: Update documentation and run the full verification suite

**Files:**
- Modify: `README-CN.md`, `README.md`

- [ ] **Step 1: Document task-specific dialogs.**

  Replace references to typing into a shared deployment input with the per-task dialog behavior; document that sudo authorization remains system-managed and is never stored by the GUI.

- [ ] **Step 2: Run the complete package tests.**

  Run:

  ```bash
  cd /home/openflex/openflex_all_new/Release-version/openflex_ws/src/openflex_integrated/openflex_gui
  QT_QPA_PLATFORM=offscreen pytest -q
  ```

  Expected: all tests pass.

- [ ] **Step 3: Build the Release workspace package.**

  Run the existing workspace build command for `openflex_gui` and verify the package installs without errors.

- [ ] **Step 4: Review the diff.**

  Confirm only the Release workspace GUI package, its tests, documentation, and the new design/plan documents changed; do not modify `/home/openflex/openflex_all/openflex_ws`.
