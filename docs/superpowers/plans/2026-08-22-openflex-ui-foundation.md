# OpenFlex UI Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the existing control center to PySide6, move the VR controls below the left-side startup workflow, give runtime diagnostics the full right column, and add a persistent day/night theme toggle.

**Architecture:** Keep all current ROS 2 commands and process lifecycle behavior unchanged during this foundation phase. Introduce a small `ThemeManager` responsible for palette selection and persistence, then adapt `MainWindow` to PySide6 and the approved two-column layout. Later plans will split the large window and embed motor management.

**Tech Stack:** Python 3.10, PySide6, ROS 2 Humble `ament_python`, `unittest`, Qt offscreen platform.

---

### Task 1: Lock PySide6 compatibility in tests

**Files:**
- Modify: `test/test_main_window_ui.py`
- Modify: `openflex_gui/main_window.py`
- Modify: `setup.py`
- Modify: `package.xml`

- [ ] **Step 1: Change the UI test import and add a Qt binding assertion**

Replace the Qt import with:

```python
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QScrollArea
```

Add this test:

```python
def test_window_uses_pyside6(self):
    self.assertTrue(type(self.window).__module__.startswith("openflex_gui"))
    self.assertEqual(QApplication.__module__.split(".")[0], "PySide6")
```

- [ ] **Step 2: Run the test to verify the old binding fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: import or binding assertion failure because `main_window.py` still uses PyQt5.

- [ ] **Step 3: Migrate the window to PySide6**

Use these imports and API forms:

```python
from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QSettings, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QTextCursor
from PySide6.QtWidgets import QApplication

class _Signals(QObject):
    log = Signal(str)
    log_html = Signal(str)
    ui = Signal(object)
```

Replace `app.exec_()` with `app.exec()`. Preserve every existing command string and process callback.

Update package metadata:

```python
install_requires=['setuptools', 'PySide6'],
```

```xml
<exec_depend>python3</exec_depend>
```

- [ ] **Step 4: Run all current UI tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: all tests pass under PySide6.

- [ ] **Step 5: Compile and build the package**

Run:

```bash
python3 -m py_compile openflex_gui/main_window.py test/test_main_window_ui.py
source /opt/ros/humble/setup.bash
colcon build --packages-select openflex_gui --symlink-install
```

Expected: Python compilation succeeds and colcon reports `1 package finished`.

### Task 2: Add a persistent day/night theme manager

**Files:**
- Create: `openflex_gui/theme_manager.py`
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [ ] **Step 1: Write theme persistence tests**

Add tests using a temporary INI-backed `QSettings`:

```python
def test_theme_manager_defaults_to_day_and_persists_night(self):
    settings = QSettings(self.settings_path, QSettings.Format.IniFormat)
    manager = ThemeManager(settings)
    self.assertEqual(manager.current_theme, "day")
    manager.set_theme("night")
    self.assertEqual(ThemeManager(settings).current_theme, "night")

def test_theme_toggle_changes_window_palette_and_tooltip(self):
    self.assertEqual(self.window.theme_manager.current_theme, "day")
    self.window.btn_theme.click()
    self.assertEqual(self.window.theme_manager.current_theme, "night")
    self.assertEqual(self.window.btn_theme.toolTip(), "切换到日间模式")
```

Change the window constructor to `MainWindow(settings: QSettings | None = None)` and pass
the temporary INI settings from `setUp`, so tests never modify real user settings.

- [ ] **Step 2: Run the new tests and confirm failure**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: failure because `ThemeManager` and `btn_theme` do not exist.

- [ ] **Step 3: Implement `ThemeManager`**

Create a focused class:

```python
class ThemeManager:
    THEMES = {"day", "night"}

    def __init__(self, settings: QSettings | None = None):
        self.settings = settings or QSettings("OpenFlex", "ControlCenter")
        saved = self.settings.value("appearance/theme", "day", type=str)
        self.current_theme = saved if saved in self.THEMES else "day"

    def set_theme(self, theme: str) -> None:
        if theme not in self.THEMES:
            raise ValueError(f"unsupported theme: {theme}")
        self.current_theme = theme
        self.settings.setValue("appearance/theme", theme)
        self.settings.sync()

    def toggle(self) -> str:
        self.set_theme("night" if self.current_theme == "day" else "day")
        return self.current_theme
```

- [ ] **Step 4: Add the icon-only theme button and two complete palettes**

Create `self.btn_theme` in the top bar with a fixed square size. Load
`QIcon.fromTheme("weather-clear-night")` while in day mode and
`QIcon.fromTheme("weather-clear")` while in night mode. If the theme icon is null, use
the fixed text fallback `N` for “switch to night” and `D` for “switch to day”. Set the
tooltip to the full Chinese action. Refactor `_apply_theme()` to choose `DAY_COLORS` or
`NIGHT_COLORS` and generate the same widget rules from palette values. Ensure the log
remains dark in both themes for readability.

- [ ] **Step 5: Run the theme tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: day default, night toggle, persistence, and all previous tests pass.

### Task 3: Implement the approved control-page layout

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [ ] **Step 1: Write layout ownership tests**

Assign object names `controlLeftColumn`, `vrCard`, and `runtimeLogCard`, then add:

```python
def test_vr_card_is_below_workflow_in_left_column(self):
    left = self.window.findChild(QFrame, "controlLeftColumn")
    vr = self.window.findChild(QFrame, "vrCard")
    self.assertIs(vr.parentWidget(), left)
    self.assertGreater(left.layout().indexOf(vr), left.layout().indexOf(
        self.window.findChild(QFrame, "controlWorkflow")
    ))

def test_runtime_log_owns_the_full_right_column(self):
    log_card = self.window.findChild(QFrame, "runtimeLogCard")
    self.assertEqual(log_card.parentWidget().objectName(), "controlRightColumn")
    self.assertEqual(log_card.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Expanding)
```

- [ ] **Step 2: Run tests and confirm the current layout fails**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: layout ownership tests fail because VR currently shares the right column with logs.

- [ ] **Step 3: Move VR below the workflow**

Build the control page as:

```python
left = QFrame()
left.setObjectName("controlLeftColumn")
left_layout = QVBoxLayout(left)
left_layout.addWidget(workflow)
left_layout.addWidget(vr_card)

right = QFrame()
right.setObjectName("controlRightColumn")
right_layout = QVBoxLayout(right)
right_layout.addWidget(log_card, 1)
```

The splitter contains only `left` and `right`. Keep the checkbox default checked and retain all existing signal connections.

- [ ] **Step 4: Make diagnostics fill the right side**

Rename the log card object to `runtimeLogCard`, set an expanding size policy, remove the old minimum-height-only behavior, and keep the 3000-block limit.

- [ ] **Step 5: Run all UI tests**

Run:

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -v -s test -p 'test_*.py'
```

Expected: all binding, theme, layout, process-independent, and legacy UI tests pass.

### Task 4: Visual and package verification

**Files:**
- Modify only if verification exposes a defect: `openflex_gui/main_window.py`
- Test: `test/test_main_window_ui.py`

- [ ] **Step 1: Run static checks**

Run:

```bash
python3 -m py_compile openflex_gui/*.py test/test_main_window_ui.py
git diff --check
```

Expected: no syntax or whitespace errors.

- [ ] **Step 2: Build and load the installed package**

Run:

```bash
source /opt/ros/humble/setup.bash
colcon build --packages-select openflex_gui --symlink-install
source install/setup.bash
QT_QPA_PLATFORM=offscreen python3 -c \
  'from openflex_gui.main_window import MainWindow; from PySide6.QtWidgets import QApplication; app=QApplication([]); w=MainWindow(); print(w.theme_manager.current_theme)'
```

Expected: build succeeds and the installed application prints `day` on a clean settings path.

- [ ] **Step 3: Capture day and night screenshots**

Render the GUI at 1024x700 and 1360x820 in both modes. Verify that the VR card is below the startup workflow, diagnostics fills the right column, the theme button remains visible, text does not overlap, and both palettes have readable contrast.

- [ ] **Step 4: Confirm backend command stability**

Compare the command/process section beginning at `_on_enable_can` with its pre-phase version. Expected: ROS launch strings, CAN helpers, process-group shutdown, camera, lidar, battery, and VR argument behavior are unchanged.

- [ ] **Step 5: Record the phase result**

Commit only the phase-one source, metadata, tests, and plan changes after all checks pass:

```bash
git add package.xml setup.py openflex_gui/main_window.py openflex_gui/theme_manager.py test/test_main_window_ui.py docs/superpowers/plans/2026-08-22-openflex-ui-foundation.md
git commit -m "feat: add PySide6 control center themes and layout"
```
