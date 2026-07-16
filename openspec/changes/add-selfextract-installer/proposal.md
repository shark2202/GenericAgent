## Why

GA 现有 linux/mac 分发产物（`.tar.gz` 便携包 + `.deb`/`.rpm`/`.pkg` 原生安装器）覆盖了"有 sudo / 用包管理器"的用户，但漏掉三类人群：无 sudo 权限的共享主机用户、airgap / 离线机器、以及想要"单文件一键装"不想解压再找脚本的用户。参考 `D:/opcteam-code/scripts/package-selfextract.sh` 的 makeself 机制（bash header + marker + tar.gz payload 追加），可为 GA 增加 linux/mac 单文件自解压安装器，零额外依赖、零特权，补齐这一受众。

## What Changes

- 新增 `scripts/package-selfextract.sh`：把 assemble 产的 `GenericAgent/` 整树打成单文件 `GenericAgent-<ver>-<arch>.install.sh`（makeself 机制：bash header + `__PAYLOAD_BELOW__` marker + tar.gz payload 追加）。
- 安装行为：`./x.install.sh` → sha256 校验 → 解压到 `~/.local/share/GenericAgent` → 修 console_script shebang → 软链 `~/.local/bin/ga` → Linux 创建 `.desktop` 进应用菜单。
- webkit 缺失检测：Linux 上若缺 `libwebkit2gtk-4.1`，打印 `apt install` 提示并**继续装**（CLI/TUI 仍可用；自动装需 sudo 违背无特权初衷）。
- `--uninstall`：清安装目录 + 软链 + `.desktop`，给 self-extract 一个干净卸载路径。
- `release-local.sh` 并入 selfextract 步（assemble → selfextract → build-installer）。
- 产物 `dist/release/GenericAgent-<ver>-<arch>.install.sh` 与现有 `.tar.gz`/`.deb`/`.rpm`/`.pkg`/`.exe` 并存。

## Capabilities

### New Capabilities

- `selfextract-installer`：linux/mac 单文件自解压安装器，makeself 机制，零特权装到 `~/.local`，带 sha256 校验 / 幂等 / shebang 修正 / `.desktop` / 卸载。

### Modified Capabilities

_(无现有 spec 需修改——这是新增分发产物，不改变 GA 核心功能需求)_

## Impact

- **代码**：新增 `scripts/package-selfextract.sh`（移植自 opcteam 版 216 行，适配 GA 入口名 + webkit 检测 + `.desktop` + `--uninstall`）；改 `scripts/release-local.sh` 串入 selfextract 步。
- **依赖**：无新依赖（makeself 机制手搓，不用 makeself CLI；`tar`/`sha256sum`/`awk`/`sed` 系统自带）。
- **产物**：新增 `.install.sh` 第三产物，与现有 5 产物并存。
- **兼容性**：完全向后兼容，新增产物不影响现有分发。
- **平台**：linux-x64 + mac-arm64 + mac-x64（Win 不做，已有 Inno `.exe`）。
