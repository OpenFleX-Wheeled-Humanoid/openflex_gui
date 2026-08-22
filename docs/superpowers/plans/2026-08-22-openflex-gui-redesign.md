# OpenFlex 发售版 GUI 改版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已确认的 v13 控制中心布局移植到发售版 PyQt5 GUI，并完整复用现有 ROS/CAN/VR/传感器后台逻辑。

**Architecture:** 保持 `MainWindow` 作为进程控制器，在同一文件内增加按职责划分的界面构建方法。使用 `QStackedWidget` 管理三页，使用 `QScrollArea` 保证小窗口可用，所有现有按钮成员名和槽函数保持不变。

**Tech Stack:** Python 3.10、PyQt5 5.15、ROS 2 ament_python、pytest。

---

### Task 1: 固定 UI 合同

**Files:**
- Create: `test/test_main_window_ui.py`
- Modify: `openflex_gui/main_window.py`

- [ ] **Step 1: 写失败测试**

测试离屏创建 `MainWindow`，断言存在 `page_stack`、三个导航按钮、默认整机页、原有后台按钮、3000 块日志限制和三个编号步骤。

- [ ] **Step 2: 验证测试先失败**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: FAIL，原因是 `page_stack` 或新版导航控件尚不存在。

- [ ] **Step 3: 实现最小页面骨架**

在 `_build_ui()` 中创建顶部栏、侧栏和 `QStackedWidget`，提供 `_set_page(index)` 统一更新导航选中状态。

- [ ] **Step 4: 验证测试通过**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: PASS。

### Task 2: 完成整机控制页

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [ ] **Step 1: 增加失败测试**

断言整机页包含一个统一流程面板，步骤顺序为 CAN、电机状态、整机控制；右侧包含 VR 卡片和日志视图。

- [ ] **Step 2: 验证测试先失败**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: FAIL，原因是统一流程区域尚未创建。

- [ ] **Step 3: 实现控制页面**

创建 `_build_control_page()` 与 `_build_control_step()`，复用 `btn_enable_can`、`btn_disable_can`、`btn_status`、`btn_bringup_start`、`btn_bringup_stop`、`btn_vr_start`、`btn_vr_stop`、`chk_vr_chassis` 和四个状态点。

- [ ] **Step 4: 验证测试通过**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: PASS。

### Task 3: 完成传感器与部署页面

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [ ] **Step 1: 增加失败测试**

断言传感器页保留五个现有相机/雷达按钮，部署页存在明确的基础占位内容。

- [ ] **Step 2: 验证测试先失败**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: FAIL，原因是两个页面尚未完整创建。

- [ ] **Step 3: 实现页面并接回槽函数**

创建 `_build_sensors_page()` 和 `_build_deploy_page()`，保持传感器按钮成员名和原有信号连接不变。

- [ ] **Step 4: 验证测试通过**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: PASS。

### Task 4: 应用主题和响应式约束

**Files:**
- Modify: `openflex_gui/main_window.py`
- Modify: `test/test_main_window_ui.py`

- [ ] **Step 1: 增加失败测试**

断言关键容器 `objectName`、窗口最小尺寸、页面滚动区域和主按钮语义属性存在。

- [ ] **Step 2: 验证测试先失败**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: FAIL，原因是视觉属性尚未应用。

- [ ] **Step 3: 加入统一样式表**

新增 `_apply_theme()`，使用设计规范中的背景、面板、边线、文字、主色、状态色和不超过 7 px 的圆角；通过 `QScrollArea` 和 layout stretch 保证窗口缩小时不重叠。

- [ ] **Step 4: 验证测试通过**

Run: `QT_QPA_PLATFORM=offscreen pytest -q test/test_main_window_ui.py`

Expected: PASS。

### Task 5: 完整验证

**Files:**
- Verify: `openflex_gui/main_window.py`
- Verify: `test/test_main_window_ui.py`

- [ ] **Step 1: Python 编译检查**

Run: `python3 -m py_compile openflex_gui/main_window.py test/test_main_window_ui.py`

Expected: exit 0。

- [ ] **Step 2: 运行全部 UI 测试**

Run: `QT_QPA_PLATFORM=offscreen pytest -q`

Expected: 全部 PASS。

- [ ] **Step 3: ROS 包构建**

Run from workspace: `colcon build --packages-select openflex_gui --symlink-install`

Expected: `Summary: 1 package finished`。

- [ ] **Step 4: 离屏渲染检查**

创建窗口、处理事件并保存 PNG；检查图像尺寸、非空像素和主区域 geometry，确认界面实际完成布局。

