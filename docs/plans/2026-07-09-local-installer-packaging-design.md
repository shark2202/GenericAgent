# 本地安装器打包设计方案（脱离 GitHub CI）

**日期**: 2026-07-09
**状态**: 设计完成，待实现
**分支**: dev

## 背景与目标

GenericAgent 现有分发走 `.github/workflows/release.yml`（tag `v*` 触发，4-arch matrix 在 per-OS runner 上跑），产出 portable archive（压缩包）+ 草稿 GitHub Release。用户决定**丢弃此 CI**，改为**本地三台主机构建**，同时保留两类产物：

1. **压缩包**（portable archive）—— 沿用现有 `assemble-dist-local.sh` 产出，解压即用。
2. **安装包**（native installer）—— 新增：Win `.exe`、mac `.pkg`、Linux `.deb` + `.rpm`。

载荷模型沿用 python-build-standalone (PBS) 自包含 CPython，**不引入 PyInstaller 冻结**（那是独立大决策，out of scope）。

## 现有机制（不改动）

- `scripts/bundle-python.sh` — **仅分发用**（dev 用 uv）。下载 PBS CPython + 用目标 Python 二进制 `pip install` GA 依赖，输出 `dist/python-bundle/<arch>/python/`。支持 win-x64 / mac-arm64 / mac-x64 / linux-x64。**关键约束**：`bundle-python.sh:89-106` 运行目标架构 Python 二进制装依赖 → bundle 是**主机绑定**（Win 跑不了 mac python）；mac-x64 在 macos-arm64 上靠 Rosetta 交叉。
- `scripts/assemble-dist-local.sh` — 打包 PBS Python + GA 源码（tracked+untracked，.gitignore respected）+ 4 launcher（run.cmd/run.sh → `launch.pyw` GUI；ga.cmd/ga.sh → `python -m ga_cli` CLI）→ `dist/release/GenericAgent-<version>-<arch>.{zip|tar.gz}`。注释：仅产"本主机能建的 arch"。**唯一改动**：`[version]` 参数的 "snapshot" 默认改为从 pyproject 读（见 §3.1）。

## §1 脚本布局（新增文件）

```
scripts/
├─ release-local.sh              # 一键入口 = bundle → assemble → build-installer
├─ build-installer.sh            # 分发器：arch → 对应平台 builder，带 host-OS 守卫
│                                #   （Win 拒绝建 mac .pkg 等）
├─ bundle-python.sh              # 不动
├─ assemble-dist-local.sh        # 仅改 version 默认值
└─ installers/
    ├─ common.sh                 # 版本读取、产物校验等共享函数
    ├─ win.iss                   # Inno Setup 脚本模板
    ├─ mac-pkg.sh                # pkgbuild + productbuild
    ├─ linux-deb.sh              # dpkg-deb
    ├─ linux-rpm.sh              # rpmbuild
    └─ TESTING.md                # 人工验收 checklist（见 §3.3）
```

**职责分离**：`assemble-dist-local.sh` 只产压缩包（单一职责不变）；`build-installer.sh` 只产安装包；`release-local.sh` 串两者。输出并存于 `dist/release/`：

```
dist/release/
├─ GenericAgent-<ver>-<arch>.zip       (win)      ┐
├─ GenericAgent-<ver>-<arch>.tar.gz    (mac/linux)├ 压缩包（assemble 产）
├─ GenericAgent-<ver>-<arch>.exe       (win)      ┐
├─ GenericAgent-<ver>-<arch>.pkg       (mac)      ├ 安装包（build-installer 产）
├─ GenericAgent-<ver>-<arch>.deb       (linux)    │
└─ GenericAgent-<ver>-<arch>.rpm       (linux)    ┘
```

## §2 各平台安装器行为

### 构建主机

Win + macOS + Linux 三台，各产自己 arch。mac arm64 机 + Rosetta 产 mac-x64。`build-installer.sh` 内 host-OS 守卫拒绝跨平台（如 Win 主机拒绝建 mac `.pkg`）。

### Win — Inno Setup `.exe`

- **默认安装位置**：`%LOCALAPPDATA%\Programs\GenericAgent`，`PrivilegesRequired=lowest`，**免 UAC**。
- **快捷方式**：开始菜单 + 可选桌面快捷方式 → `pythonw.exe launch.pyw`（pythonw 无控制台闪窗；图标走 `.lnk` 的 `IconFilename`）。
- **PATH**：安装目录加进**用户** PATH → `ga` 任意终端可用。`run` 不进 PATH（避免与系统命令冲突）。
- **卸载**：Inno 自动卸载器清文件 + PATH + 快捷方式。
- **自定义**：
  - Inno 标准目的地页编辑框 → 任意文件夹（零成本，Inno 自带）。
  - 静态参数 `/ALLUSERS=1`（或单独 admin build）→ 装到 `Program Files` 并触发 UAC，给要全局装的企业用户。
  - 两者都是 Inno 原生，不写额外脚本。

### mac — `.pkg`（pkgbuild + productbuild，macOS 自带）

- **默认安装位置**：`/Applications/GenericAgent.app`，自包含 bundle。
  - `Contents/Resources/{python/, 源码, launch.pyw}`
  - `Contents/MacOS/GenericAgent` 是 `#!/bin/sh` 脚本，exec 内部 python。
  - `Info.plist` + `icon.icns` 给 Launchpad 图标。
- **CLI 软链**：postinstall 软链 `/usr/local/bin/ga` → `.app/Contents/Resources/ga.sh`。
- **权限**：`.pkg` 需管理员（mac 标准惯例）。
- **卸载**：`.pkg` 无自动卸载 → 装目录内置 `uninstall.sh`。
- **自定义**：
  - `productbuild` 的 `distribution.xml` 加 `domains` 元素 → 允许用户选安装卷（mac 原生）。
  - `postinstall` 的 `ga` CLI 软链做成 **optional choice**（choices outline 勾选框），不想污染 `/usr/local/bin` 的用户可取消。
  - `.app` 内部全相对路径 → 装后可手动拖到任意位置，GUI 仍能用（仅 CLI 软链会断，README 注明）。
  - 不搞任意文件夹的 `.pkg` 安装器——mac 惯例是 `/Applications`，YAGNI。

### Linux — `.deb` + `.rpm`（dpkg-deb / rpmbuild，发行版自带）

- **默认安装位置**：`/opt/GenericAgent`。
- **桌面入口**：`/usr/share/applications/genericagent.desktop`（`Exec=/opt/GenericAgent/run.sh`，`Icon=` 随包 png）进应用菜单。
- **CLI 软链**：postinst 软链 `/usr/local/bin/ga` → `/opt/GenericAgent/ga.sh`。
- **依赖声明**：pywebview 需 GTK/webkit2gtk → `.deb` 声明 `libwebkit2gtk-4.1-0`、`gir1.2-gtk-3.0`；`.rpm` 声明对应 `webkit2gtk4.1`、`gtk3`。**不降级 TUI 躲依赖**。
- **卸载**：`apt remove` / `dnf remove` 原生卸载 + post-rm 清理软链。
- **自定义**：走 FHS 惯例不强问位置；`ga` 软链可用 `update-alternatives` 让用户切到别的 bin 目录。要改路径的高级用户通常重打包，安装器内不兜底。

### 跨平台约定

- **默认启动 = `launch.pyw`**（pywebview + streamlit GUI 窗口），**不是 tui2/tui_v3**。理由：安装器受众是非技术用户期待 GUI 窗口；mac `.app` 从 Dock 启无 PTY，Textual/prompt_toolkit TUI 会炸；Win 双击 TUI 弹黑窗体验差；Linux tui 唯一优势是躲 GTK 依赖但正确解法是声明包依赖。tui2/tui_v3 作为次要 CLI 入口保留。
- **不引导填 API key**：现有 launcher 已在首启 `cp mykey_template.jsonc → mykey.jsonc`。
- **Native launcher shim 不需要新开发**：现有 `run.cmd`/`run.sh`/`ga.cmd`/`ga.sh` 即 shim（带 `cd` 到自身目录，装到任意位置假设仍成立）。Win 用 `.lnk` 指向 `pythonw.exe`；mac `.app` 主程序可是 shell 脚本（macOS 能 exec）；Linux `.desktop` 的 `Exec=` 指 `run.sh`。

**自定义原则**：所有"自定义"都走平台原生机制（Inno wizard / pkg domains / deb FHS），不发明新开关。

## §3.1 版本号方案

**单一真相源 = `pyproject.toml` `[project].version`**（当前 `0.1.0`，需 bump）。

- `[project].version` 是规范串，`uv`/pip 认、`importlib.metadata.version("genericagent")` 运行时可读（`ga --version` 直接出）。
- `assemble-dist-local.sh` 的 `[version]` 参数默认改为**从 pyproject 读**（替掉 `"snapshot"`）；分发包里没 `.git` 也能读到。
- Release 时打 tag `v<version>` 镜像 pyproject（如 `v0.2.0`），保留历史语义。
- **dev 构建**（非 tag 提交）：`<version>+g<sha7>[.dirty]`，如 `0.1.0+g9f3a2b.dirty`，让本机构建不与正式版撞号。
- **格式**：semver，pre-1.0 期 `0.x.y`，稳定后升 1.0。README 用日期记 changelog 条目，与版本号无关。
- **4 平台元数据全从同一串派生**：
  - Inno `AppVersion` = `0.2.0`
  - pkg `version` = `0.2.0`
  - deb `Version: 0.2.0-1`（debian-rev 固定 `-1`，dev 后缀的 `+` 合法）
  - rpm `Version: 0.2.0` + `Release: 1`（dev 后缀用 `~`，如 `0.2.0~dev1`）

被放弃的备选：git tag 为真相源（分发包无 `.git` 读不到 `ga --version`）；独立 VERSION 文件（对纯 Python 多余一层）。

## §3.2 代码签名

**首发策略：三平台都不签名。**

- **Win**：未签名 `.exe` SmartScreen 拦一次"未知发布者"，用户点"仍要运行"即可。per-user 装机 + 用户已习惯跳过。**可接受首发**。
- **mac**：未签名 `.pkg`/`.app` 首启需右键打开或 `xattr -d com.apple.quarantine`。README 注明。不签名时 `.app` 主程序是 shell 脚本也没事（§2 future caveat 解除）。
- **Linux**：`.deb`/`.rpm` 无签名门槛，`apt install ./x.deb` / `dnf install ./x.rpm` 原生支持本地包。**零障碍**。

**脚本预留**：`build-installer.sh` 加 `--sign <cert-ref>` 可选参数；无参数 = 不签名。证书/凭据走环境变量（`GA_SIGN_CERT`、`GA_NOTARY_APPLE_ID` 等），**不进仓库**。签名逻辑**不实现**（YAGNI，避免写死未测代码）。

**未来补强优先级**：mac 签名（Developer ID + `notarytool` 公证，Apple Developer $99/年）> Win EV 证书 > Linux GPG（仅建仓库分发时才有意义）。

## §3.3 测试策略

### ① 构建产物自检（脚本侧，自动化）

`build-installer.sh` 末尾 `verify` 阶段：
- 校验产物文件存在且非空。
- 解包/列出内容：Inno `/VERYSILENT /DIR=temp` 装到临时目录看清单；`.pkg` 用 `pkgutil --expand`；`.deb` `dpkg-deb -c`；`.rpm` `rpm -qlp`。
- 跑 `python -c "import importlib.metadata; print(importlib.metadata.version('genericagent'))"` 验版本串对得上。
- 每台构建主机上跑，零人工、防"产物空/版本错"。

### ② 安装行为验收（人工 checklist）

不写 VM e2e 自动化（编排成本高，YAGNI），改 `scripts/installers/TESTING.md` 人工 checklist，每版本跑一遍打勾：
- 通用：装 → 启 GUI（双击快捷方式/.app/.desktop）→ `ga --version` 终端可用 → 卸载干净（无残留文件/PATH/软链/快捷方式）。
- Win 额外：per-user 免 UAC、`/ALLUSERS=1` 走 UAC、自定义目录、卸载清 PATH。
- mac 额外：右键打开首启、`/usr/local/bin/ga` 软链、移走 `.app` 后 GUI 仍能开。
- Linux 额外：`apt remove`/`dnf remove` 清干净、`.desktop` 进菜单、缺 `libwebkit2gtk` 时依赖声明生效。

### ③ 回归保护

- 现有 `pytest tests/` + `ruff check .` 照常跑——安装器不动 Python 源码。
- `scripts/installers/*.sh` 加 `shellcheck`（在 AGENTS.md 登记命令）。
- `release-local.sh --dry-run`：只 bundle + assemble，不调 build-installer，验证到压缩包产出为止。

## 未来项（不在本次实现范围）

- mac 代码签名/公证时，Gatekeeper 对"主程序是 shell 脚本"的 `.app` 偶有脾气，届时或需补 ~20 行 C stub 当 `.app` 主程序（不签名则不需要）。
- Win EV 代码签名证书 + SmartScreen 消除。
- 建 APT/YUM 仓库后给 `.deb`/`.rpm` 加 GPG 签名。

## 独立小项（与安装器解耦）

- 把 `tui_v3` 注册进 `ga_cli/cli.py`，让 `ga tui3` 可用（当前 `tui_v3` 是推荐 TUI 但未注册，`ga tui2` 反而注册了）。
- 给 `pyproject.toml` `[project].version` 做一次 bump（从 `0.1.0` 起步）。

## 待办（实现顺序）

1. 写 `scripts/installers/common.sh`（版本读取函数 + 产物校验函数）。
2. 改 `scripts/assemble-dist-local.sh` version 默认为从 pyproject 读。
3. 写 `scripts/installers/win.iss` + `build-installer.sh` Win 分支。
4. 写 `scripts/installers/mac-pkg.sh`。
5. 写 `scripts/installers/linux-deb.sh` + `linux-rpm.sh`。
6. 写 `scripts/release-local.sh`（串 bundle → assemble → build-installer，带 `--dry-run`）。
7. 写 `scripts/installers/TESTING.md`。
8. 在 `AGENTS.md` 登记 `shellcheck scripts/installers/` 命令。
