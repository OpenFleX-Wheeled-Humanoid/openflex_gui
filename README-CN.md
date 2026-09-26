# openflex_gui

[English](./README.md) | 中文

---

![封面](./image/cover.png)


基于 PySide6 的 OpenFlex 全身 VR 控制系统管理界面。

## 概述

桌面控制面板，用于管理 CAN 总线接口、检查电机状态、启动/停止集成机器人 bringup 和 VR 遥操作启动文件。设计目的是让操作员无需输入终端命令即可启动整个机器人系统。

界面包含整机控制、电机管理、传感器和部署中心四个页面。部署中心的每张任务卡片都对应脚本中的一个入口；需要参数的任务会打开专用配置对话框，确认后再调用脚本，不再提供通用任务输入框。

部署参数按任务区分：VR 安装选择 Pico/Quest 并确认 USB 连接，相机配置填写四个相机序列号，雷达配置填写网络尾段，KCAN 配置选择是否卸载 PCAN。下载与更新、环境安装和编译直接确认执行。任务运行过程中只有检测到 sudo 提示时才会显示专用密码对话框，密码仅写入当前 PTY，不会写入 GUI 日志或配置文件。

## 功能

- **CAN 总线管理**：启用/禁用全部 6 个 CAN 接口（can0-can5），覆盖手臂、头部、升降台和底盘
- **电机状态检查**：查询所有电机子系统（升降台 CANopen、底盘 RS06 转向、底盘 UM 轮毂电机、双臂、头部）
- **整机控制**：启动完整的机器人控制栈（`integrated_robot_bringup.launch.py`）
- **VR 遥操作**：启动/停止 VR 遥操，可选底盘控制模式
- **组合启动**：独立启动并停止整机控制与 VR 遥操作；不改变 CAN 启用状态，默认使用 `vr_chassis:=true`
- **电量监控**：通过串口接口显示电池电量
- **进程管理**：使用 SIGINT 优雅关闭，超时后强制终止，关闭前失能底盘

## CAN 配置

| 接口 | 波特率 | 功能 |
|------|--------|------|
| `can0` | 1 Mbps | 右臂（Robstride） |
| `can1` | 1 Mbps | 左臂（Robstride） |
| `can2` | 1 Mbps | 头部（Robstride） |
| `can3` | 1 Mbps | 升降台（CANopen） |
| `can4` | 1 Mbps | 底盘驱动（UM 轮毂电机） |
| `can5` | 1 Mbps | 底盘转向（RS06） |

## 使用方法

```bash
# 编译

---
cd ~/openflex_all/openflex_ws
colcon build --packages-select openflex_gui

# 运行

---
ros2 run openflex_gui openflex_gui
```

整机控制区域还提供“启动整机控制+VR”和“停止整机控制+VR”。组合启动不会启用或禁用 CAN；它启动整机控制后每 5 秒检查控制器状态，控制器就绪后启动 VR。VR 默认使用已勾选的“VR 控制底盘速度”选项。

## 前置条件

- Python 3、PySide6
- ROS 2 工作空间已 source
- CAN 辅助脚本（`enable_can_helper.sh`、`disable_can_helper.sh`）
- `openarmx_arm_driver` 包（用于电机状态查询）
- `openarmx_integrated_bringup` 包（用于机器人启动）
- `openarmx_teleop_vr`、`openflex_vr_bridge`、`swerve_bringup` 包（用于 VR 遥操启动）

## 许可证

本作品采用知识共享 署名-非商业性使用-相同方式共享 4.0 国际许可协议 (CC BY-NC-SA 4.0) 进行许可。

版权所有 (c) 2026 成都长数机器人有限公司 (Chengdu Changshu Robot Co., Ltd.)

详情请参阅 [LICENSE_CN.md](LICENSE) 文件或访问：http://creativecommons.org/licenses/by-nc-sa/4.0/

## 致谢

本包是 OpenArmX 机器人平台生态系统的一部分，专为协作机器人领域的研究和工业应用而开发。

---

## 📞 联系我们

### 成都长数机器人有限公司
**Chengdu Changshu Robotics Co., Ltd.**

| 联系方式 | 信息 |
|---------|------|
| 📧 邮箱 | openarmrobot@gmail.com |
| 📱 电话/微信 | +86-17746530375 |
| 🌐 官网 | <https://openarmx.com/> |
| 🌐 文档 | <http://docs.openarmx.com/> |
| 📍 地址 | 天津经济技术开发区西区新业八街11号华诚机械厂 |
| 👤 联系人 | 王先生 |
