# OpenFlex Motor Management Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed all existing head, dual-arm, lift-column, and chassis controls from `openflex_manager` into a new “电机管理” page in the PySide6 control center without opening a second window.

**Architecture:** Refactor the manager UI into a reusable `MotorManagementPage(QWidget)` while retaining `OpenFlexMainWindow` as a thin standalone wrapper. Load the sibling source application through one controlled adapter, attach its existing `MainController` to the embedded page, synchronize the global theme, and run manager shutdown from the control center lifecycle.

**Tech Stack:** Python 3.10, PySide6, `openflex_driver`, ROS 2 Humble, `unittest`, Qt offscreen platform.

---

### Task 1: Make the manager UI embeddable

**Files:**
- Modify: `../openflex_manager/ui/main_window.py`
- Modify: `../openflex_manager/ui/__init__.py`
- Modify: `../openflex_manager/main.py`
- Create: `../openflex_manager/tests/test_embedded_page.py`

- [x] **Step 1: Write a failing embedded-page test**

Create a `unittest.TestCase` that constructs `MotorManagementPage` offscreen and asserts:

```python
self.assertIsInstance(page, QWidget)
self.assertNotIsInstance(page, QMainWindow)
self.assertEqual(page.left_tabs.count(), 4)
self.assertEqual(
    [page.left_tabs.tabText(index) for index in range(4)],
    ["头部", "双臂", "升降台", "底盘"],
)
self.assertTrue(hasattr(page, "btn_start_can"))
self.assertTrue(hasattr(page, "btn_chassis_estop"))
```

- [x] **Step 2: Verify the test fails because `MotorManagementPage` is missing**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s tests -p 'test_*.py'
```

Expected: the new test fails while importing `MotorManagementPage`.

- [x] **Step 3: Refactor the manager UI class**

Rename the existing UI implementation to `MotorManagementPage(QWidget)`. Build its root
layout directly on `self` and add a `QMenuBar` widget instead of calling
`QMainWindow.menuBar()` or `setCentralWidget()`. Keep every existing public control
attribute and page builder unchanged so all controllers continue to bind to the same API.

Add a thin wrapper:

```python
class OpenFlexMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.motor_page = MotorManagementPage()
        self.setCentralWidget(self.motor_page)

    def closeEvent(self, event):
        self.motor_page.shutdown()
        event.accept()
```

Expose `MotorManagementPage` and `OpenFlexMainWindow` from `ui/__init__.py`.

- [x] **Step 4: Preserve standalone startup**

Update `main.py` so the controller binds to `window.motor_page`, then assign the controller
to both the wrapper and the page. Showing `OpenFlexMainWindow` must still start the original
standalone manager.

- [x] **Step 5: Run manager tests and an offscreen standalone smoke test**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s tests -p 'test_*.py'
QT_QPA_PLATFORM=offscreen timeout 3s python3 main.py
```

Expected: embedded-page tests pass; standalone startup reaches its event loop and is stopped
by `timeout` rather than crashing.

### Task 2: Add a controlled manager adapter to the ROS GUI package

**Files:**
- Create: `openflex_gui/motor_manager_adapter.py`
- Create: `test/test_motor_manager_adapter.py`

- [x] **Step 1: Write adapter path and construction tests**

The test obtains the sibling manager directory from the release workspace, constructs the
adapter with that explicit path, and verifies:

```python
adapter = MotorManagerAdapter(manager_dir)
page = adapter.create_page()
self.assertEqual(type(page).__name__, "MotorManagementPage")
self.assertEqual(page.left_tabs.count(), 4)
```

The test calls `adapter.shutdown()` and confirms repeated shutdown is harmless.

- [x] **Step 2: Verify the adapter test fails**

Run the GUI test suite and expect failure because `motor_manager_adapter.py` is missing.

- [x] **Step 3: Implement deterministic source loading**

`MotorManagerAdapter` accepts a manager directory, validates `main.py`, `ui/`,
`controllers/`, and `config/`, and inserts only that absolute directory at the front of
`sys.path`. It imports `MotorManagementPage`, creates one page, and never launches another
`QApplication`. Hardware-facing `MainController` construction is deferred until the user
opens the motor-management page while ROS control processes are stopped.

Provide:

```python
def create_page(self) -> QWidget
def initialize_controller(self)
def set_theme(self, theme: str) -> None
def has_active_connection(self) -> bool
def shutdown(self) -> None
```

Map control-center `day` to manager `light`, and `night` to manager `dark`.

- [x] **Step 4: Run adapter and manager tests**

Expected: four-tab page construction, theme mapping, and idempotent shutdown pass without
starting CAN.

### Task 3: Add the “电机管理” navigation page

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [x] **Step 1: Write failing navigation and embedding tests**

Update the expected navigation to:

```python
["整机控制", "电机管理", "传感器", "部署中心"]
```

Assert four pages, and assert `window.motor_page` is the exact widget stored by
`window.motor_manager_adapter`.

- [x] **Step 2: Verify tests fail with the current three-page navigation**

Run all GUI tests. Expected: navigation count and labels fail.

- [x] **Step 3: Create and insert the manager page**

Construct `MotorManagerAdapter` after workspace discovery, call `create_page()`, insert the
page directly into `page_stack` between control and sensors, and add the matching nav label.
Do not wrap this page in the outer `QScrollArea`; its own tabs and split layouts manage the
available area.

If manager loading fails, show a non-interactive error page containing the missing path or
dependency, keep the rest of the control center usable, and record the exception in runtime
diagnostics.

- [x] **Step 4: Synchronize the global theme**

After every global theme application, call:

```python
self.motor_manager_adapter.set_theme(self.theme_manager.current_theme)
```

Hide the manager-local theme button so the top-bar sun/moon button is the single theme
control.

- [x] **Step 5: Run all manager and GUI tests**

Expected: the new page is embedded, all four subsystem tabs exist, and prior control,
sensor, theme, VR, and log tests remain green.

### Task 4: Add lifecycle cleanup and initial safety interlock

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `openflex_gui/motor_manager_adapter.py`
- Modify: `test/test_motor_manager_adapter.py`

- [x] **Step 1: Write failing safety and shutdown tests**

Test that `has_active_connection()` returns true after the manager controller is initialized.
This strict rule includes the two arm buses, which are opened eagerly by `Robot`. Test that
repeated adapter shutdown invokes the manager controller once.

Add a GUI test with a fake active adapter and assert `_on_start_bringup()` returns before
creating a ROS process and logs an explicit maintenance-mode conflict.

- [x] **Step 2: Verify the tests fail**

Expected: active connection detection and idempotent shutdown tests fail before the adapter
implementation is completed.

- [x] **Step 3: Implement active-connection detection and shutdown**

Treat an initialized `MainController` as maintenance-active because it creates the two arm
CAN buses immediately. Shutdown is guarded by an internal flag and delegates to the existing
`MainController.shutdown()`. Leaving the motor page shuts down the adapter and rebuilds a
clean, uninitialized page so ROS control can subsequently own the hardware.

- [x] **Step 4: Block conflicting ROS starts**

Before starting bringup or VR, check the adapter. If maintenance is active, leave all process
references unchanged, set the relevant status indicator to error, and log that direct motor
connections must be closed first.

While bringup or VR is running, disable the embedded motor page. Re-enable it after both
processes stop. This is the initial strict interlock; later runtime-coordinator work will also
detect externally started ROS processes.

- [x] **Step 5: Integrate manager cleanup into window close**

After stopping control-center child processes, call adapter shutdown. Manager shutdown stops
monitoring and motion, disables supported devices, and closes its direct hardware links.

- [x] **Step 6: Run all tests, build, and visually verify**

Run manager tests, GUI tests, Python compilation, `git diff --check`, and:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select openflex_gui --symlink-install
```

Capture the embedded motor page at 1360x820 in day and night modes. Verify all four tabs are
reachable, the global theme button updates the page, and no second top-level window appears.
