#![windows_subsystem = "windows"]

use eframe::egui;
use serde::Deserialize;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{Duration, Instant};

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
struct StatusSnapshot {
    running: bool,
    status_text: String,
    device_name: String,
    received_folder: String,
    log_directory: String,
    beacon: bool,
    still_copy: bool,
    #[serde(default = "default_true")]
    notify_sound: bool,
    #[serde(default = "default_true")]
    notify_toast: bool,
    autostart: bool,
    send_to: bool,
    receive_mode: String,
    #[serde(default)]
    media_format: String,
    #[serde(default)]
    ble_hashes: String,
    #[serde(default)]
    my_identifiers: String,
    apple_identity_present: bool,
    contact_count: u32,
    adapter_name: String,
    adapter_pid: String,
    device_id: String,
    driver_service: String,
    winusb_bound: bool,
    target_is_up: bool,
    would_lose_all_network: bool,
    pid: u32,
}

#[derive(PartialEq, Eq, Clone, Copy)]
enum Tab {
    Home,
    Driver,
    Contacts,
    Settings,
}

struct DropBridgeApp {
    root: PathBuf,
    data_dir: PathBuf,
    tab: Tab,
    status: StatusSnapshot,
    last_poll: Instant,
    name_input: String,
    name_dirty: bool,
    contact_input: String,
    my_id_input: String,
    my_id_dirty: bool,
    driver_ack: bool,
    toast_msg: String,
}

impl DropBridgeApp {
    fn new(cc: &eframe::CreationContext<'_>, root: PathBuf, data_dir: PathBuf) -> Self {
        install_chinese_font(&cc.egui_ctx);
        let status = read_status(&data_dir);
        let name_input = if status.device_name.is_empty() {
            "DropBridge".to_string()
        } else {
            status.device_name.clone()
        };
        let my_id_input = status.my_identifiers.clone();
        Self {
            root,
            data_dir,
            tab: Tab::Home,
            status,
            last_poll: Instant::now() - Duration::from_secs(5),
            name_input,
            name_dirty: false,
            contact_input: String::new(),
            my_id_input,
            my_id_dirty: false,
            driver_ack: false,
            toast_msg: String::new(),
        }
    }

    fn supervisor_exe(&self) -> PathBuf {
        for candidate in &[
            self.root.join("DropBridge.exe"),
            self.root.join("backend").join("DropBridge.exe"),
            self.root
                .join("build")
                .join("supervisor")
                .join("Release")
                .join("DropBridge.exe"),
        ] {
            if candidate.exists() {
                return candidate.clone();
            }
        }
        self.root.join("DropBridge.exe")
    }

    fn send_ui_cmd(&mut self, query: &str) {
        let exe = self.supervisor_exe();
        if !exe.exists() {
            self.toast_msg = format!("未找到 {:?}", exe);
            return;
        }
        let uri = format!("dropbridge://ui?{}", query);
        let _ = Command::new(&exe).arg(uri).spawn();
        self.last_poll = Instant::now() - Duration::from_secs(5);
    }

    fn launch_send(&mut self, files: &[String]) {
        let exe = self.supervisor_exe();
        if !exe.exists() {
            self.toast_msg = format!("未找到 {:?}", exe);
            return;
        }
        let mut cmd = Command::new(&exe);
        cmd.arg("--send");
        for f in files {
            cmd.arg(f);
        }
        let _ = cmd.spawn();
    }

    fn run_driver_action(&mut self, arg: &str) {
        let exe = self.supervisor_exe();
        if !exe.exists() {
            self.toast_msg = format!("未找到 {:?}", exe);
            return;
        }
        let log = self.data_dir.join("logs").join("driver-install.txt");
        let _ = Command::new(&exe)
            .arg(arg)
            .arg("--root")
            .arg(&self.root)
            .arg("--log")
            .arg(&log)
            .spawn();
        self.toast_msg = "已发起驱动操作，请按系统管理员提示确认。".to_string();
    }

    fn ensure_backend_silent(&mut self) {
        let exe = self.supervisor_exe();
        if exe.exists() {
            let _ = Command::new(&exe).arg("--silent").spawn();
            self.toast_msg = "正在后台启动托盘服务…".to_string();
        }
    }
}

impl eframe::App for DropBridgeApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        if self.last_poll.elapsed() >= Duration::from_millis(800) {
            self.last_poll = Instant::now();
            self.status = read_status(&self.data_dir);
            if !self.name_dirty && !self.status.device_name.is_empty() {
                self.name_input = self.status.device_name.clone();
            }
            if !self.my_id_dirty && !self.status.my_identifiers.is_empty() {
                self.my_id_input = self.status.my_identifiers.clone();
            }
        }
        ctx.request_repaint_after(Duration::from_millis(900));

        // Handle drag & drop files anywhere on the window
        let dropped: Vec<String> = ctx.input(|i| {
            i.raw
                .dropped_files
                .iter()
                .filter_map(|f| f.path.as_ref().map(|p| p.to_string_lossy().to_string()))
                .collect()
        });
        if !dropped.is_empty() {
            self.launch_send(&dropped);
        }

        egui::TopBottomPanel::top("top_nav").show(ctx, |ui| {
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                ui.heading("DropBridge");
                ui.add_space(12.0);
                ui.selectable_value(&mut self.tab, Tab::Home, "收发使用");
                ui.selectable_value(&mut self.tab, Tab::Driver, "安装与驱动");
                ui.selectable_value(&mut self.tab, Tab::Contacts, "联系人验证");
                ui.selectable_value(&mut self.tab, Tab::Settings, "设置与静默自启");
            });
            ui.add_space(6.0);
        });

        egui::TopBottomPanel::bottom("bottom_bar").show(ctx, |ui| {
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                if !self.toast_msg.is_empty() {
                    ui.label(&self.toast_msg);
                } else {
                    ui.label("关闭此窗口后，DropBridge 仍会在系统托盘静默运行。");
                }
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    if ui.button("完全退出服务").clicked() {
                        self.send_ui_cmd("cmd=exit");
                        ctx.send_viewport_cmd(egui::ViewportCommand::Close);
                    }
                });
            });
            ui.add_space(4.0);
        });

        egui::CentralPanel::default().show(ctx, |ui| {
            egui::ScrollArea::vertical().show(ui, |ui| {
                match self.tab {
                    Tab::Home => self.ui_home(ui),
                    Tab::Driver => self.ui_driver(ui),
                    Tab::Contacts => self.ui_contacts(ui),
                    Tab::Settings => self.ui_settings(ui),
                }
            });
        });
    }
}

impl DropBridgeApp {
    fn ui_home(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("运行状态");
            ui.add_space(4.0);
            let status_line = if self.status.status_text.is_empty() {
                "托盘服务未运行或正在初始化".to_string()
            } else {
                self.status.status_text.clone()
            };
            let color = if self.status.running {
                egui::Color32::from_rgb(22, 163, 74)
            } else {
                egui::Color32::from_rgb(217, 119, 6)
            };
            ui.colored_label(color, status_line);
            ui.label(format!(
                "设备名称：{}   |   驱动：{}",
                if self.status.device_name.is_empty() {
                    "DropBridge"
                } else {
                    &self.status.device_name
                },
                if self.status.winusb_bound {
                    "WinUSB 已就绪"
                } else if self.status.adapter_name.is_empty() {
                    "未检测到受支持网卡"
                } else {
                    "尚未绑定 WinUSB（请前往“安装与驱动”）"
                }
            ));
            ui.add_space(6.0);
            ui.horizontal(|ui| {
                if self.status.pid == 0 {
                    if ui.button("后台启动托盘服务").clicked() {
                        self.ensure_backend_silent();
                    }
                } else {
                    if (ui.button("唤醒附近 iPhone（BLE 脉冲）")).clicked() {
                        self.send_ui_cmd("cmd=pulse_beacon");
                        self.toast_msg = "已发送 BLE 唤醒脉冲，请重新打开 iPhone 隔空投送面板。".to_string();
                    }
                    if ui.button("重新启动后端").clicked() {
                        self.send_ui_cmd("cmd=restart");
                    }
                    if ui.button("重置链路").clicked() {
                        self.send_ui_cmd("cmd=reset");
                    }
                }
                if ui.button("打开日志").clicked() {
                    self.send_ui_cmd("cmd=open_logs");
                }
            });
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("接收文件（iPhone → 电脑）");
            ui.label("在 iPhone 分享面板选择「隔空投送」→ DropBridge，无需更改手机接收范围。");
            ui.label(format!(
                "保存位置：{}",
                if self.status.received_folder.is_empty() {
                    "data\\received"
                } else {
                    &self.status.received_folder
                }
            ));
            ui.add_space(4.0);
            if ui.button("打开接收文件夹").clicked() {
                self.send_ui_cmd("cmd=open_received");
            }
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("发送文件（电脑 → iPhone）");
            ui.label("请先将 iPhone 隔空投送临时设为「所有人（10 分钟）」。也可以直接把文件拖进本窗口。");
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                if ui.button("选择文件并发送…").clicked() {
                    if let Some(paths) = rfd::FileDialog::new().pick_files() {
                        let strs: Vec<String> =
                            paths.into_iter().map(|p| p.to_string_lossy().to_string()).collect();
                        self.launch_send(&strs);
                    }
                }
                if ui.button("打开设备发现窗口").clicked() {
                    self.launch_send(&[]);
                }
            });
        });
    }

    fn ui_driver(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("专用无线网卡识别");
            ui.add_space(4.0);
            if self.status.adapter_name.is_empty() {
                ui.colored_label(
                    egui::Color32::from_rgb(217, 119, 6),
                    "未检测到受支持的网卡（仅支持 RTL8821CU 0bda:c820 或 RTL8811CU 0bda:c811）。",
                );
                ui.label("DropBridge 不会改动您电脑上的其他无线或有线网卡。请直插主板后置 USB 口。");
            } else {
                ui.label(format!(
                    "识别型号：{} ({})",
                    self.status.adapter_name, self.status.adapter_pid
                ));
                ui.label(format!("实例 ID ：{}", self.status.device_id));
                ui.label(format!(
                    "当前驱动：{}",
                    if self.status.driver_service.is_empty() {
                        "无"
                    } else {
                        &self.status.driver_service
                    }
                ));
            }
        });

        ui.add_space(10.0);

        if !self.status.adapter_name.is_empty() && !self.status.winusb_bound {
            ui.group(|ui| {
                ui.set_min_width(ui.available_width());
                ui.heading("绑定 WinUSB 驱动（首次使用）");
                if self.status.would_lose_all_network {
                    ui.colored_label(
                        egui::Color32::from_rgb(220, 38, 38),
                        "⚠ 高危警告：该网卡当前正在联网，且未检测到其他已连接网络！绑定后将无法连普通 Wi-Fi，继续操作会导致断网。",
                    );
                } else if self.status.target_is_up {
                    ui.colored_label(
                        egui::Color32::from_rgb(217, 119, 6),
                        "注意：该网卡当前处于已连接状态；绑定后将专用于 DropBridge，电脑会改用其他网络。",
                    );
                } else {
                    ui.colored_label(
                        egui::Color32::from_rgb(22, 163, 74),
                        "✓ 该网卡当前未用于上网，绑定不会影响电脑现有网络。",
                    );
                }
                ui.add_space(6.0);
                ui.checkbox(
                    &mut self.driver_ack,
                    "我已知晓绑定后该网卡将专用于 DropBridge，且随时可一键恢复原厂 Wi-Fi 驱动",
                );
                ui.add_space(4.0);
                ui.add_enabled_ui(self.driver_ack, |ui| {
                    if ui.button("一键安装 WinUSB 驱动").clicked() {
                        self.run_driver_action("--install-driver");
                    }
                });
            });
        } else if self.status.winusb_bound {
            ui.group(|ui| {
                ui.set_min_width(ui.available_width());
                ui.heading("驱动状态：已绑定 WinUSB");
                ui.label("如需让该 USB 网卡重新连接普通 Wi-Fi，可随时一键恢复。");
                ui.add_space(4.0);
                if ui.button("一键恢复原厂 Wi-Fi 驱动").clicked() {
                    self.run_driver_action("--restore-driver");
                }
            });
        }
    }

    fn ui_contacts(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("Apple 身份证书（仅限联系人模式）");
            ui.add_space(4.0);
            if self.status.apple_identity_present {
                ui.colored_label(
                    egui::Color32::from_rgb(22, 163, 74),
                    "✓ 已导入 Apple 身份证书（接收和发送均会附带签名记录）",
                );
                if ui.button("清除已导入证书").clicked() {
                    self.send_ui_cmd("cmd=clear_identity");
                    self.toast_msg = "已清除 Apple 身份证书，恢复为所有人模式。".to_string();
                }
            } else {
                ui.colored_label(
                    egui::Color32::from_rgb(217, 119, 6),
                    "未导入 Apple 身份证书（当前仅可使用「所有人」模式）",
                );
                ui.label("请从 Mac 导出 certificate.der、key.pk8.der、validation_record.cms（可选 intermediate.der / contact_hashes.txt）放入同一文件夹后导入。");
                ui.add_space(4.0);
                if ui.button("选择证书文件夹并导入…").clicked() {
                    if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                        let encoded = url_encode(&dir.to_string_lossy());
                        self.send_ui_cmd(&format!("cmd=import_identity&dir={}", encoded));
                        self.toast_msg = "已提交导入证书请求。".to_string();
                    }
                }
            }
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("我的 Apple ID 标识（BLE 唤醒哈希）");
            ui.add_space(4.0);
            ui.label("填写您的 Apple ID 邮箱或手机号（11 位大陆手机号自动补全 +86，多个用逗号分隔）。保存后会写入蓝牙广播，帮助「仅限联系人」模式下的 iPhone 发现本机。");
            ui.horizontal(|ui| {
                if ui.text_edit_singleline(&mut self.my_id_input).changed() {
                    self.my_id_dirty = true;
                }
                if ui.button("保存并刷新广播").clicked() {
                    let encoded = url_encode(self.my_id_input.trim());
                    self.my_id_dirty = false;
                    self.send_ui_cmd(&format!("cmd=set_my_ids&ids={}", encoded));
                    self.toast_msg = "已更新本机 BLE 唤醒哈希并重启蓝牙广播。".to_string();
                }
            });
            if !self.status.ble_hashes.is_empty() {
                ui.label(format!("当前 BLE 广播哈希前缀：{}", self.status.ble_hashes));
            }
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("接收策略与媒体格式");
            ui.add_space(4.0);
            let is_auto = self.status.receive_mode == "auto"
                || self.status.receive_mode == "everyone"
                || self.status.receive_mode.is_empty();
            let is_ask = self.status.receive_mode == "ask";
            let is_contacts = self.status.receive_mode == "contacts";
            if ui.radio(is_auto, "所有人（自动接收并保存）").clicked() {
                self.send_ui_cmd("cmd=set_receive_mode&mode=auto");
            }
            if ui.radio(is_ask, "所有人（接收前弹窗询问确认）").clicked() {
                self.send_ui_cmd("cmd=set_receive_mode&mode=ask");
            }
            ui.add_enabled_ui(self.status.apple_identity_present, |ui| {
                if ui
                    .radio(is_contacts, "仅限联系人（仅接收信任联系人列表发来的文件）")
                    .clicked()
                {
                    self.send_ui_cmd("cmd=set_receive_mode&mode=contacts");
                }
            });

            ui.add_space(6.0);
            ui.separator();
            ui.add_space(4.0);
            let is_original = self.status.media_format == "original";
            ui.label("照片与视频格式偏好：");
            ui.horizontal(|ui| {
                if ui.radio(!is_original, "兼容格式（推荐：自动转为 JPEG / H.264）").clicked() {
                    self.send_ui_cmd("cmd=set_media_format&format=compatible");
                }
                if ui.radio(is_original, "原始格式（保留 iPhone 原片 HEIC / HEVC）").clicked() {
                    self.send_ui_cmd("cmd=set_media_format&format=original");
                }
            });
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("信任联系人管理");
            ui.add_space(4.0);
            ui.label(format!("当前已保存信任联系人哈希：{} 条", self.status.contact_count));
            ui.horizontal(|ui| {
                ui.label("手机号 / Apple ID 邮箱：");
                ui.text_edit_singleline(&mut self.contact_input);
                if ui.button("添加").clicked() && !self.contact_input.trim().is_empty() {
                    let encoded = url_encode(self.contact_input.trim());
                    self.send_ui_cmd(&format!("cmd=add_contact&contact={}", encoded));
                    self.contact_input.clear();
                    self.toast_msg = "已添加信任联系人哈希。".to_string();
                }
            });
            ui.add_space(4.0);
            if ui.button("清空信任联系人").clicked() {
                self.send_ui_cmd("cmd=clear_contacts");
                self.toast_msg = "已清空信任联系人。".to_string();
            }
        });
    }

    fn ui_settings(&mut self, ui: &mut egui::Ui) {
        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("静默开机自启与系统集成");
            ui.add_space(4.0);
            let mut autostart = self.status.autostart;
            if ui
                .checkbox(
                    &mut autostart,
                    "开机自启动（静默运行：不出现主界面，仅在系统托盘后台待命）",
                )
                .changed()
            {
                self.send_ui_cmd(if autostart {
                    "cmd=autostart_on"
                } else {
                    "cmd=autostart_off"
                });
            }

            let mut send_to = self.status.send_to;
            if ui
                .checkbox(&mut send_to, "添加到资源管理器右键「发送到」菜单")
                .changed()
            {
                self.send_ui_cmd(if send_to {
                    "cmd=sendto_on"
                } else {
                    "cmd=sendto_off"
                });
            }
        });

        ui.add_space(10.0);

        ui.group(|ui| {
            ui.set_min_width(ui.available_width());
            ui.heading("隔空投送偏好设置");
            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.label("设备名称：");
                if ui.text_edit_singleline(&mut self.name_input).changed() {
                    self.name_dirty = true;
                }
                if ui.button("应用").clicked() {
                    let encoded = url_encode(&self.name_input);
                    self.name_dirty = false;
                    self.send_ui_cmd(&format!("cmd=rename&name={}", encoded));
                }
            });

            ui.add_space(4.0);
            ui.horizontal(|ui| {
                ui.label(format!(
                    "接收目录：{}",
                    if self.status.received_folder.is_empty() {
                        "data\\received"
                    } else {
                        &self.status.received_folder
                    }
                ));
                if ui.button("更改…").clicked() {
                    if let Some(dir) = rfd::FileDialog::new().pick_folder() {
                        let encoded = url_encode(&dir.to_string_lossy());
                        self.send_ui_cmd(&format!("cmd=set_folder&folder={}", encoded));
                    }
                }
            });

            ui.add_space(4.0);
            let mut beacon = self.status.beacon;
            if ui
                .checkbox(&mut beacon, "开启蓝牙 Beacon 广播（帮 iPhone 更快发现电脑）")
                .changed()
            {
                self.send_ui_cmd(if beacon {
                    "cmd=beacon_on"
                } else {
                    "cmd=beacon_off"
                });
            }

            let mut still = self.status.still_copy;
            if ui
                .checkbox(&mut still, "收到实况照片时自动生成 -still.jpg 静图副本")
                .changed()
            {
                self.send_ui_cmd(if still {
                    "cmd=still_on"
                } else {
                    "cmd=still_off"
                });
            }

            ui.add_space(6.0);
            ui.separator();
            ui.add_space(4.0);
            ui.label("接收提示与通知：");

            let mut notify_sound = self.status.notify_sound;
            if ui
                .checkbox(&mut notify_sound, "收到文件时播放提示音")
                .changed()
            {
                self.send_ui_cmd(if notify_sound {
                    "cmd=notify_sound_on"
                } else {
                    "cmd=notify_sound_off"
                });
            }

            let mut notify_toast = self.status.notify_toast;
            if ui
                .checkbox(&mut notify_toast, "收到文件时显示右下角系统通知（含图片复制按钮）")
                .changed()
            {
                self.send_ui_cmd(if notify_toast {
                    "cmd=notify_toast_on"
                } else {
                    "cmd=notify_toast_off"
                });
            }
        });
    }
}

fn resolve_root() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            if dir.join("backend").exists() || dir.join("data").exists() {
                return dir.to_path_buf();
            }
            if let Some(p1) = dir.parent() {
                if let Some(p2) = p1.parent() {
                    if p2.join("src").exists() {
                        return p2.to_path_buf();
                    }
                }
            }
            return dir.to_path_buf();
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn resolve_paths() -> (PathBuf, PathBuf) {
    let mut root_arg: Option<PathBuf> = None;
    let mut data_arg: Option<PathBuf> = None;
    let args: Vec<String> = std::env::args().collect();
    let mut i = 1;
    while i < args.len() {
        if args[i] == "--root" && i + 1 < args.len() {
            root_arg = Some(PathBuf::from(&args[i + 1]));
            i += 2;
        } else if args[i] == "--data" && i + 1 < args.len() {
            data_arg = Some(PathBuf::from(&args[i + 1]));
            i += 2;
        } else {
            i += 1;
        }
    }

    let root = root_arg.unwrap_or_else(resolve_root);
    let data = data_arg.unwrap_or_else(|| {
        if let Some(parent) = root.parent() {
            if parent.file_name().and_then(|n| n.to_str()) == Some("app") {
                if let Some(base) = parent.parent() {
                    return base.join("data");
                }
            }
        }
        root.join("data")
    });
    (root, data)
}

fn read_status(data_dir: &Path) -> StatusSnapshot {
    let path = data_dir.join("status.json");
    if let Ok(text) = fs::read_to_string(&path) {
        if let Ok(parsed) = serde_json::from_str::<StatusSnapshot>(&text) {
            return parsed;
        }
    }
    StatusSnapshot::default()
}

fn url_encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.as_bytes() {
        let ch = *b as char;
        if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' || ch == '.' || ch == '~' {
            out.push(ch);
        } else {
            out.push_str(&format!("%{:02X}", b));
        }
    }
    out
}

fn install_chinese_font(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    for candidate in &[
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyh.ttf",
        r"C:\Windows\Fonts\simhei.ttf",
    ] {
        if let Ok(bytes) = fs::read(candidate) {
            fonts.font_data.insert(
                "cjk".to_owned(),
                egui::FontData::from_owned(bytes),
            );
            fonts
                .families
                .entry(egui::FontFamily::Proportional)
                .or_default()
                .insert(0, "cjk".to_owned());
            fonts
                .families
                .entry(egui::FontFamily::Monospace)
                .or_default()
                .push("cjk".to_owned());
            break;
        }
    }
    ctx.set_fonts(fonts);
}

fn main() -> eframe::Result<()> {
    let (root, data_dir) = resolve_paths();
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([620.0, 500.0])
            .with_min_inner_size([520.0, 420.0]),
        ..Default::default()
    };
    eframe::run_native(
        "DropBridge",
        options,
        Box::new(move |cc| Ok(Box::new(DropBridgeApp::new(cc, root, data_dir)))),
    )
}
