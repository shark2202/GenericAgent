## Context

GA 现有 linux/mac 分发链（`release-local.sh`：`bundle-python.sh` → `assemble-dist-local.sh` → `build-installer.sh`）产出 `.tar.gz` 便携包 + `.deb`/`.rpm`/`.pkg` 原生安装器。这三类产物都假设用户有 sudo 或愿意用包管理器，漏掉了无 sudo / airgap / 想要单文件一键装的人群。

参考实现 `D:/opcteam-code/scripts/package-selfextract.sh`（216 行）已用 makeself 机制（bash header + `__PAYLOAD_BELOW__` marker + tar.gz payload 追加）验证可行。GA 与 opcteam 的根本差异：opcteam 是 go:embed 代码进单二进制、self-extract 只装 `python/`；GA 是纯 Python 项目、self-extract 必须装 assemble 产的整棵 `GenericAgent/` 树（python + 源码 + `launch.pyw` + 4 launcher）。

约束：零额外依赖（不用 makeself CLI，手搓 marker+payload）、零特权（装 `~/.local`）、版本沿用 §3.1（pyproject 真相源 + dev 后缀）、与现有 5 产物并存不替换。

## Goals / Non-Goals

**Goals:**
- linux/mac 单文件 `GenericAgent-<ver>-<arch>.install.sh`，`./x.install.sh` 一键装到 `~/.local`，零 sudo。
- sha256 自校验防下载损坏；幂等（重装清旧）；修 PBS console_script shebang；Linux 建 `.desktop` 进菜单。
- 并入 `release-local.sh` 一键流程。

**Non-Goals:**
- 不做 Windows 版（Win 对应物是 7z-SFX/.bat，独立决策；已有 Inno `.exe`）。
- 不替换现有 `.tar.gz`/`.deb`/`.rpm`/`.pkg`/`.exe`。
- 不引入加密 payload 变体（opcteam `python-b1`/`encrypt-bundle` 留未来）。
- 不自动装系统库（`libwebkit2gtk`）——违背无特权初衷。
- 不产 mac `.app`/Launchpad 入口（mac GUI 用户用 `.pkg`）。

## Decisions

### D1 — payload = assemble 的 `GenericAgent/` 整树
opcteam 只装 `python/`（代码 go:embed 进二进制）。GA 纯 Python，不带源码跑不起来 → payload 是 `assemble-dist-local.sh` 产的 `GenericAgent/`（python/ + 源码 + `launch.pyw` + run.sh/ga.sh 等）。体积更大（~169MB vs opcteam 更小）但必需。
**备选**：只装 `python/` + 运行时拉源码——否决，违背 airgap/离线目标。

### D2 — makeself 机制手搓，不依赖 makeself CLI
结构：`#!/usr/bin/env bash` + `PAYLOAD_SHA256=<sha>` 注入 + header heredoc（`exit 0` 在 marker 前，payload 字节永不被 shell 解析）+ `__PAYLOAD_BELOW__` marker 行 + `cat payload.tar.gz` 追加。运行时 `awk` 定位 marker → `tail -n +$START` 抽 payload → sha256 校验 → `tar -xzf` 解压。
**备选**：用 `makeself` CLI——否决，引入额外构建依赖；手搓 ~200 行已由 opcteam 验证足够。

### D3 — Linux 建 `.desktop` 进应用菜单
装到 `~/.local/share/applications/genericagent.desktop`（`Exec=$INSTALL_DIR/run.sh`，`Icon=$INSTALL_DIR/ga.png`），`update-desktop-database`。opcteam 不做（它是 TUI/CLI）；GA 默认 GUI=pywebview，装 `~/.local` 没 `.desktop` 用户找不到 GUI 入口。

### D4 — webkit 缺失：打印提示 + 继续装
Linux 上 `ldconfig -p | grep libwebkit2gtk` 检测；缺失则打印 `apt install libwebkit2gtk-4.1-0`（或 dnf 等价）并**继续**安装。**不退出**（CLI/TUI 仍可用）、**不自动装**（需 sudo，违背无特权初衷）。
**备选**：缺失即退出——否决，用户连 CLI 都没有；自动 `sudo apt install`——否决，违背无特权。

### D5 — `--uninstall` 自带卸载
opcteam 没有卸载（它靠重装覆盖）。self-extract 没有包管理器卸载路径 → 加 `--uninstall`：清 `$INSTALL_DIR` + `~/.local/bin/ga` 软链 + `.desktop`。

### D6 — mac 软链 `~/.local/bin`（无 sudo）
和"覆盖无 sudo 人群"目标一致。要 `/usr/local/bin` 的用户用 `.pkg`（需管理员，mac 标准惯例）。

### D7 — 安装默认目录 `~/.local/share/GenericAgent`，`ga` → `~/.local/bin`
FHS user-local 约定，零特权。`--install-dir`/`--bin-dir` 可覆盖。

### D8 — shebang fix 做（移植 opcteam step 6）
PBS pip 装依赖生成的 `python/bin/*` console_script shebang 是构建绝对路径，换位置就断。GA 的 `run.sh`/`ga.sh` 已用相对路径（`cd "$(dirname "$0")"` + `python/bin/python3`）躲过，但做了更稳（直接调 `python/bin/ga` 也不断）。GNU/BSD `sed -i` 兼容：`sed --version | grep GNU` 分支。

### D9 — GNU/BSD tar 兼容
打 payload 时 `--transform` 重命名 `SRC_DIR`→`python` 是 GNU tar only。fallback：建临时 symlink `python`→`SRC_DIR`，`tar -h` 跟随（移植 opcteam line 50-59）。

### D10 — 并入 `release-local.sh`，顺序 assemble → selfextract → build-installer
都依赖 assemble 产物，串行最简单。`release-local.sh` 在 assemble 后、build-installer 前插 selfextract 步。`--dry-run` 止于 assemble（不跑 selfextract/build-installer）。

### D11 — 版本显示沿用 §3.1
文件名用全 VERSION（含 `+g<sha7>[.dirty]` dev 后缀）；`--version` 显示 base + payload sha256 前 16 位。与现有 5 产物一致。

## Risks / Trade-offs

- **[webkit 不能自动装]** → 打印 `apt`/`dnf` 提示 + 继续；CLI/TUI 无 webkit 仍可用。用户为 GUI 须手动装系统库——这是无特权方案的固有代价，已接受。
- **[sha256 防损坏不防篡改]** → header 里的 `PAYLOAD_SHA256` 与 payload 同文件，攻击者改 payload 可重算 sha。真正防篡改需外部 GPG 签 `.sh`（未来项，与 §3.2 不签名首发一致）。当前 sha256 只防下载损坏/意外截断。
- **[payload 体积大]** → 整树 ~169MB（含 PBS python + 源码）。opcteam 更小但模型不同。已接受；`--check` 让用户下载后先验完整性。
- **[mac 无 `.app`/Launchpad]** → by design，mac GUI 用户走 `.pkg`。self-extract 在 mac 是 CLI/portable 选项，面向高级用户。
- **[marker 碰撞]** → `__PAYLOAD_BELOW__` 足够唯一（opcteam 同款），`awk` 精确匹配整行。低风险。
- **[sed/tar 跨平台]** → D8/D9 的 GNU/BSD 分支已覆盖 linux(GNU)/mac(BSD)。WSL 构建用 GNU，无问题。

## Migration Plan

新产物，无迁移。回滚：删 `scripts/package-selfextract.sh` + 从 `release-local.sh` 摘除 selfextract 步（改回 assemble→build-installer）。已分发的 `.install.sh` 仍可独立运行（自包含）。

## Open Questions

无——7 项决策已全拍（见澄清摘要），实现细节（shebang fix/tar 兼容/版本显示/顺序）已定。
