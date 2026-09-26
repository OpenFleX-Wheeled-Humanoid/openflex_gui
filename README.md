# openflex_gui

English | [中文](./README-CN.md)

---

![Cover](./image/cover.png)


PySide6-based GUI application for managing the OpenFlex whole-body VR control system.

## Overview

A desktop control panel that manages CAN bus interfaces, checks motor status, and starts/stops the integrated robot bringup and VR teleoperation launch files. Designed for operators to bring the entire robot system online without typing terminal commands.

The interface contains four pages: integrated control, motor management, sensors, and deployment center. Each deployment card maps to one installer-script entry point. Tasks that need parameters open a dedicated configuration dialog before confirmation; there is no shared free-form task input box.

Deployment parameters are task-specific: VR installation selects Pico or Quest and confirms USB connection, camera setup collects four camera serial numbers, lidar setup collects network suffixes, and KCAN setup selects whether to uninstall PCAN. Source sync, environment installation, and compilation run after confirmation without extra fields. A dedicated password dialog appears only when the running task emits a sudo prompt; the password is sent to the current PTY and never written to GUI logs or configuration files.

## Features

- **CAN Bus Management**: Enable/disable all 6 CAN interfaces (can0-can5) for arms, head, lift, and chassis
- **Motor Status Check**: Query all motor subsystems (lift CANopen, chassis RS06 steering, chassis UM hub motors, dual arms, head)
- **Integrated Bringup**: Launch the full robot control stack (`integrated_robot_bringup.launch.py`)
- **VR Teleoperation**: Start/stop VR teleop with optional chassis control mode
- **Combined Start**: Independently start/stop bringup and VR teleop without changing CAN state; defaults to `vr_chassis:=true`
- **Battery Monitor**: Display battery level via serial interface
- **Process Management**: Graceful shutdown with SIGINT, forced kill after timeout, chassis deactivation before shutdown

## CAN Configuration

| Interface | Baudrate | Function |
|-----------|----------|----------|
| `can0` | 1 Mbps | Right arm (Robstride) |
| `can1` | 1 Mbps | Left arm (Robstride) |
| `can2` | 1 Mbps | Head (Robstride) |
| `can3` | 1 Mbps | Lift (CANopen) |
| `can4` | 1 Mbps | Chassis driving (UM hub motors) |
| `can5` | 1 Mbps | Chassis steering (RS06) |

## Usage

```bash
# Build

---
cd ~/openflex_all/openflex_ws
colcon build --packages-select openflex_gui

# Run

---
ros2 run openflex_gui openflex_gui
```

The integrated-control area also provides “启动整机控制+VR” and “停止整机控制+VR”. The combined flow does not enable or disable CAN; it starts bringup, checks controller readiness every five seconds, and starts VR once the controllers are ready. VR uses the checked chassis-speed option by default.

## Prerequisites

- Python 3, PySide6
- ROS 2 workspace sourced
- CAN helper scripts (`enable_can_helper.sh`, `disable_can_helper.sh`)
- `openarmx_arm_driver` package (for motor status queries)
- `openarmx_integrated_bringup` package (for robot launch)
- `openarmx_teleop_vr`, `openflex_vr_bridge`, and `swerve_bringup` packages (for VR teleop launch)

## License

This work is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International License (CC BY-NC-SA 4.0).

Copyright (c) 2026 Chengdu Changshu Robot Co., Ltd. (成都长数机器人有限公司)

For more details, see the [LICENSE](LICENSE) file or visit: http://creativecommons.org/licenses/by-nc-sa/4.0/

## Acknowledgments

This package is part of the OpenArmX robotic platform ecosystem, developed for research and industrial applications in collaborative robotics.

---

## 📞 Contact Us

### Chengdu Changshu Robot Co., Ltd.

| Contact           | Information                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------ |
| 📧 Email          | [openarmrobot@gmail.com](mailto:openarmrobot@gmail.com)                                                      |
| 📱 Phone / WeChat | +86-17746530375                                                                                              |
| 🌐 Website        | [https://openarmx.com/](https://openarmx.com/)                                                               |
| 🌐 Documentation  | [http://docs.openarmx.com/](http://docs.openarmx.com/)                                                               |
| 📍 Address        | Huacheng Machinery Plant, No.11 Xinye 8th Street, West Area, Tianjin Economic-Technological Development Area |
| 👤 Contact Person | Mr. Wang                                                                                                     |
