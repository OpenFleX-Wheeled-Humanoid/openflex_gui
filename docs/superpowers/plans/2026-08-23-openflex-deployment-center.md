# OpenFlex Deployment Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the motor page's dark initial frame and replace the deployment placeholder with a safe GUI for the supported OpenFlex installer tasks, excluding desktop-launcher changes.

**Architecture:** `MotorManagerAdapter` stores the requested theme before constructing the embedded page. Deployment behavior is isolated in `deployment_runner.py`: a fixed task catalog validates every script argument, direct `QProcess` handles compile and dry-run tasks, and GNOME Terminal handles privileged or interactive tasks while writing log/PID/status files. `MainWindow` owns only page widgets, user confirmation, runtime interlocks, and rendering deployment state.

**Tech Stack:** Python 3.10, PySide6, QProcess, QTemporaryDir, GNOME Terminal, ROS 2 Humble, unittest.

---

### Task 1: Apply the correct motor theme before first display

**Files:**
- Modify: `../openflex_manager/ui/main_window.py`
- Modify: `../openflex_manager/tests/test_embedded_page.py`
- Modify: `openflex_gui/motor_manager_adapter.py`
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_motor_manager_adapter.py`

- [x] **Step 1: Write failing initial-theme tests**

Assert that `MotorManagementPage()` starts in `light`, and that calling `adapter.set_theme("night")` before `create_page()` creates a page whose first observable theme is `dark`.

- [x] **Step 2: Run focused tests and confirm failure**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s tests -p 'test_embedded_page.py'
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_motor_manager_adapter.py'
```

Expected: the manager reports `dark` by default and the adapter ignores a theme set before page construction.

- [x] **Step 3: Implement pre-construction theme state**

Give `MotorManagementPage` an `initial_theme="light"` argument and validate it against `{"light", "dark"}`. Store the control-center theme in `MotorManagerAdapter`, make `set_theme()` update that pending value, and construct the page with the mapped manager theme. In `_build_motor_management_page()`, call `adapter.set_theme(...)` before `adapter.create_page()`.

- [x] **Step 4: Run theme and GUI tests**

Expected: day mode is light from construction, persisted night mode is dark from construction, and all existing theme tests remain green.

- [x] **Step 5: Commit**

```bash
git commit -m "fix: apply motor theme before first display"
```

### Task 2: Define and validate deployment tasks

**Files:**
- Create: `openflex_gui/deployment_runner.py`
- Create: `test/test_deployment_runner.py`

- [x] **Step 1: Write failing catalog tests**

Test the exact task IDs `openflex`, `environment`, `compile`, `kcan`, `vr`, `camera`, `lidar`, and `sync-source`. Assert that `desktop` and arbitrary values raise `ValueError`, jobs accept only integers from 1 through 32, and dry-run commands never require a terminal.

- [x] **Step 2: Verify the new tests fail because the module is absent**

Run:

```bash
python3 -m unittest discover -v -s test -p 'test_deployment_runner.py'
```

- [x] **Step 3: Implement the immutable task catalog**

Create frozen `DeploymentTask` records containing `task_id`, Chinese label, script flag, risk description, and `terminal_required`. Create `DeploymentCommandBuilder` that validates the installer path and returns argument arrays only:

```python
[str(script), "--compile", "--jobs", "4"]
[str(script), "--kcan", "--dry-run"]
```

Never accept free-form shell fragments.

- [x] **Step 4: Implement runner state and signals**

Create a `QObject` runner with `output`, `state_changed`, and `finished` signals. Expose `start(task_id, dry_run, jobs)`, `cancel()`, `is_running`, and `active_task`. Reject a second task while one is active.

- [x] **Step 5: Run catalog tests and commit**

```bash
git commit -m "feat: add validated deployment task runner"
```

### Task 3: Execute direct and terminal deployment tasks safely

**Files:**
- Modify: `openflex_gui/deployment_runner.py`
- Modify: `test/test_deployment_runner.py`
- Create: `openflex_gui/deployment_terminal_bridge.py`

- [x] **Step 1: Write failing process-strategy tests**

Use fake process factories to assert that compile and every dry-run use the direct strategy, while actual privileged or interactive tasks use the terminal strategy. Assert that `OPENFLEX_WORKSPACE` is the selected release workspace and that output is emitted line by line.

- [x] **Step 2: Implement direct execution**

Start the installer directly with `QProcess`, merged channels, explicit working directory, and an environment containing `OPENFLEX_WORKSPACE`. Decode incremental output, propagate the real exit code, and restore idle state after completion.

- [x] **Step 3: Implement the terminal bridge**

The bridge receives only validated positional arguments. It writes its process group ID, streams stdout/stderr through `tee` to a task-specific temporary log, and writes the final exit code atomically. Launch it through:

```text
gnome-terminal --wait -- /usr/bin/python3 deployment_terminal_bridge.py ...
```

The GUI polls appended log bytes and the status file without handling terminal input or passwords.

- [x] **Step 4: Implement cancellation**

Direct tasks receive SIGINT through their process group. Terminal tasks read the validated numeric process-group file and send SIGINT; after three seconds, a still-running group receives SIGTERM. Missing or malformed PID files must never be passed to `os.killpg`.

- [x] **Step 5: Run runner tests and commit**

```bash
git commit -m "feat: run deployment tasks with terminal privilege flow"
```

### Task 4: Replace the deployment placeholder with the functional page

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [x] **Step 1: Write failing deployment-page tests**

Inject a fake deployment runner and assert the page contains eight task choices, a dry-run checkbox, jobs control, preview/run/cancel buttons, status text, and a bounded log. Assert no desktop task appears.

- [x] **Step 2: Implement the page layout**

Use one unframed task-selection region, a compact execution toolbar, and a full-width deployment log. Risk labels distinguish build, system installation, driver, and device configuration. Buttons update without resizing the layout.

- [x] **Step 3: Bind preview, confirmation, run, and cancel**

Preview prints the exact validated command without execution. Actual non-dry-run tasks show a confirmation containing task name, risk summary, workspace, and whether a terminal opens. Run and cancel delegate only to the injected runner.

- [x] **Step 4: Bind runner output and completion**

Append timestamped output, cap the document at 3000 blocks, display running/success/failure/cancelled state, and restore all controls after completion.

- [x] **Step 5: Run GUI tests and commit**

```bash
git commit -m "feat: add deployment center controls"
```

### Task 5: Add deployment interlocks and close cleanup

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [x] **Step 1: Write failing interlock tests**

Assert deployment cannot start while bringup, VR, sensors, or motor maintenance owns hardware. Assert CAN, control, VR, sensor, and motor-management actions are disabled while deployment runs. Assert window close cancels one active deployment task.

- [x] **Step 2: Implement the runtime checks**

Centralize `_deployment_conflict_reason()` and `_update_deployment_lock()`. Do not silently stop another mode; show the exact conflict and leave all process references unchanged.

- [x] **Step 3: Add close cleanup**

Call `deployment_runner.cancel()` before existing process cleanup. The runner owns its timeout and process-group termination.

- [x] **Step 4: Run all automated tests**

Run manager tests, GUI tests with `ResourceWarning` promoted to errors, Python compilation, and `git diff --check`.

- [x] **Step 5: Build and visually verify**

Run:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select openflex_gui --symlink-install
```

Capture day/night screenshots at 1360x820 and 1024x700. Verify the motor page has no dark first state in day mode, deployment controls fit without overlap, preview works, and a dry-run completes without changing the system.

- [x] **Step 6: Commit**

```bash
git commit -m "feat: coordinate deployment with robot runtime"
```
