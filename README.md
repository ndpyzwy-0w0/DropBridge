# DropBridge

让 Windows 电脑接收 iPhone 用系统自带「隔空投送」（AirDrop）发来的文件。iPhone 不用装 App，电脑不依赖 WSL2 或虚拟机。程序常驻托盘，通过 WinUSB 直接驱动一张专用的 **RTL8821CU USB 无线网卡**，在用户态跑 AWDL；电脑原有的 Wi-Fi 不受影响。

> **状态：内部测试版，只能接收。** 真机实测 29,456,959 B 视频用时 26.84 s（约 1.10 MB/s），落盘哈希一致。Windows → iPhone 发送已确认无法实现（Q48）。Q68–Q70 的源码修复尚未在 Windows 上编译测试，暂不能对外分发。见[状态与限制](#状态与限制)。

## 使用

**需要：** Windows 10/11 x64（自带 .NET Framework 4.8 即可）；USB ID 为 `0bda:c820` 的 RTL8821CU 网卡（只验证过这个 ID，建议直插不经 Hub）；首次运行一次管理员权限；电脑自带蓝牙（建议有，用于发 beacon 帮 iPhone 发现电脑，没有也能收但更慢）。

**安装：**

1. 把 `DropBridge-portable.zip` 解压到可写目录（如 `D:\DropBridge`，不要放 Program Files）。
2. 插上网卡，双击 `DropBridge.exe`，程序只在通知区域放一个托盘图标。
3. 首次运行会询问并通过 UAC 把网卡的 **MI_02（Wi-Fi）接口**换成 WinUSB（MI_00/MI_01 不动），日志在 `data\logs\driver-install.txt`。失败时可用托盘「安装驱动（首次使用）」重试，或按 `setup\README.txt`（仓库中 [packaging/setup/README.txt](packaging/setup/README.txt)）用 Zadig 手动绑定。
4. 等待 40–90 秒网卡初始化，托盘提示就绪。首次成功启动会自动开启「开机自启」。

**接收：** iPhone 分享 → 隔空投送 → 选 **DropBridge**。文件默认存到 `data\received\`，收到后弹通知（图片可一键复制到剪贴板）；实况照片和 HEIC 默认额外生成 `<原名>-still.jpg`。

**托盘菜单**（右键）：查看状态、打开/更改接收文件夹、打开日志、复制诊断信息、开机自启、安装驱动、修改设备名称、清理通知注册信息、蓝牙 beacon 开关、实况图单图副本开关、重新启动后端、重置链路、退出。后端异常退出会自动重启，连续失败 5 次后停止，需手动「重新启动后端」。设置保存在 `data\settings.ini`。

### 数据放在哪

程序数据都在程序文件夹内，不写 `%LOCALAPPDATA%`，不装服务：

```text
DropBridge\
  DropBridge.exe   托盘程序，唯一入口
  backend\         radio / bridge / awdld / awdl_netd / python / receiver / beacon.ps1
  setup\           WinUSB 驱动包；手动绑定用的 Bind-WinUSB.cmd + Zadig
  tools\           Start-Chain.ps1（控制台调试，默认 dry-run）等
  data\            settings.ini、keys\（升级时保留）、received\、logs\<会话>\
```

写入系统的只有：HKCU 开机自启项、通知用的开始菜单快捷方式和 `dropbridge://` 协议、WinUSB 驱动包及其自签证书。

**升级：** 覆盖 `DropBridge.exe` 和 `backend\`，保留 `data\`。
**卸载：** 托盘里取消「开机自启」、点「清理通知注册信息」、退出，删除文件夹。恢复原驱动见 [docs/BINDING_RUNBOOK.md](docs/BINDING_RUNBOOK.md)。

### 常见问题

- **手机看不到 DropBridge**：确认状态为「运行中」、beacon 开着，再试「重新启动后端」。正常时 `awdld.log` 里有 `peers 1`。已知传完一次后可能连不上，重启后端可恢复（Q51）。
- **提示「网卡没响应，请拔插」**：拔插网卡后点「重新启动后端」。
- **提示「链路速率偏低」**：点「重置链路」。两台 iPhone 同时开隔空投送会降到约 0.1 MB/s（Q51）。
- **SmartScreen 警告**：程序没有代码签名，属预期。

## 开发

### 工作原理

```text
iPhone ──AWDL──▶ RTL8821CU（MI_02 = WinUSB）
  └▶ airbridge_radio.exe    devourer 用户态驱动（libusb），TCP 49876
  └▶ radio_pipe_bridge.exe  命名管道 ↔ TCP
  └▶ awdld.exe              OWL 的 AWDL 控制面（打补丁，走 IPC 后端）
  └▶ awdl_netd.exe          lwIP 用户态网络栈、mDNS 与 TCP 8771 代理
  └▶ receiver.py            AirDrop TLS / HTTP / DVZip 接收、落盘
```

`DropBridge.exe`（C# WinForms）负责按序拉起后端、看门狗、设置、通知和驱动安装。各组件为独立进程，避免 GPL-2.0（devourer）与 GPL-3.0（OWL）代码混合。固定配置为信道 149 / 20 MHz、Windows 担任同步主控、`social` 信道序列、TCP 合并开启，均由真机对比确定，背景见 [docs/FINAL_PLAN.md](docs/FINAL_PLAN.md) 和 [docs/STATUS.md](docs/STATUS.md)。

### 仓库结构

```text
src/        supervisor（托盘）、sfx、radio_usb、awdl_netd、beacon
patches/    对 devourer、WinAWDL、OWL 的补丁
tools/      构建、打包、诊断脚本和测试
packaging/  包内 README、WinUSB 驱动包、手动绑定工具
docs/       计划、审计、排障记录、工作队列
evidence/   日志、抓包摘要、测试记录
upstream/   上游原样副本（不入库），按 docs/sources.lock.json 恢复
```

### 从源码构建

需要 Windows 10/11 x64、Visual Studio 2022（MSVC + CMake）、PowerShell、Git。依赖都放在 `build\deps\`，不改系统 PATH。

1. 按 [docs/sources.lock.json](docs/sources.lock.json) 把五个上游仓库克隆到 `upstream\<name>`（winawdl 需带子模块），运行 `.\tools\Apply-AwdlPatches.ps1`。
2. `.\tools\Setup-BuildDeps.ps1 -WithOpenSsl`；手动放置 lwIP 与 lwip-contrib 到 `build\deps\`（Q50 用 `STABLE-2_2_1_RELEASE`），以及 Python 3.12 embeddable + `cryptography` 到 `build\python-embed`。
3. 编译：

   ```powershell
   .\tools\Build-Radio.ps1
   .\tools\Build-Awdl.ps1
   cmake -S src\awdl_netd -B build\awdl_netd -A x64
   cmake --build build\awdl_netd --config Release
   ctest --test-dir build\awdl_netd -C Release
   .\tools\Build-Supervisor.ps1 -Test
   ```

4. 打包：`.\tools\Package-Distributable.ps1`，然后 `dist\DropBridge\DropBridge.exe --check` 应 exit 0。

细节见 [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md) 和 [docs/DISTRIBUTION.md](docs/DISTRIBUTION.md)。诊断脚本（`Inspect-Hardware.ps1`、`Check-Binding.ps1`、`Collect-Session.ps1`、`analyze_session.py` 等）在 `tools/`；初始化网卡、发射无线帧、换驱动均需用户授权，见 [AGENTS.md](AGENTS.md)。

### 文档

- [docs/WORK_QUEUE.md](docs/WORK_QUEUE.md)：**进度以此为准**；[WORK_LOG.md](WORK_LOG.md)：工作记录。
- [docs/STATUS.md](docs/STATUS.md)：状态快照；[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)：发布前核对清单。
- [docs/FINAL_PLAN.md](docs/FINAL_PLAN.md)、[docs/PLAN.md](docs/PLAN.md)、[docs/PHASE0_AUDIT.md](docs/PHASE0_AUDIT.md)：方案、计划与审计。
- [docs/LICENSE_MATRIX.md](docs/LICENSE_MATRIX.md)：许可证核查；[docs/SEND_PATH.md](docs/SEND_PATH.md)：发送方向结论。

## 状态与限制

源码实现、本机编译、本机硬件、真实文件传输四层分开记录。当前 HEAD 含 Q68–Q70 修复，**尚未编译和测试**，与最后一次测过的包（Q50 起）不同。

| 实测（真实文件字节 / 墙钟时间） | 结果 | 出处 |
| --- | --- | --- |
| 1,207,111 B 图片 | 1.23 s（0.98 MB/s） | Q45 |
| 29,456,959 B 视频 | 26.84 s（1.10 MB/s） | Q46 / Q54 |
| 两台 iPhone 同时在线 | 0.08–0.12 MB/s | Q51 |

**未解决：** 只能接收（Q48）；首次传输后发现丢失与多机降速（Q51）；24 小时常驻测试未完成（Q7）；`/Discover` 分块解析过严（Q43）；驱动「未绑定 → 已绑定」未真机验证（Q53）。

**对外分发前还缺：** Realtek 固件再分发授权、代码签名、干净机器验收（Q13）、WinUSB 回滚演练、包内第三方许可证文本补全。详见 [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)。

## 许可证

本仓库原创代码和文档采用 [MIT](LICENSE)。第三方组件、`patches/` 补丁及链接 GPL 代码的二进制（`airbridge_radio.exe` 为 GPL-2.0，`awdld.exe` 为 GPL-3.0-or-later）保持各自许可证，因此发布包整体不能当作 MIT 分发。范围见 [NOTICE](NOTICE) 和 [docs/LICENSE_MATRIX.md](docs/LICENSE_MATRIX.md)。

项目原名 AirBridge32，对用户显示名自 2026-10-30 起为 DropBridge（Q49）；内部组件仍沿用 airbridge 前缀。
