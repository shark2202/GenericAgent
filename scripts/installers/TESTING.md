# Installer Acceptance Checklist

Human checklist for each platform installer, run **before tagging a release**.
The build scripts have an automated self-check (`build-installer.sh` verify stage:
artifact exists + contents listed); this file covers what scripts can't verify —
the on-disk install/launch/uninstall experience.

Built the installer via `./scripts/release-local.sh <arch>` first, then work the
relevant platform section below. Tick each box; a release is green only when all
boxes for its platform are ticked.

## General (every platform)

- [ ] Install succeeds (no error dialog / non-zero exit).
- [ ] Launch the GUI: double-click the start-menu / Launchpad / app-menu entry →
      a pywebview window opens showing the streamlit UI.
- [ ] `ga --version` in a fresh terminal prints the version matching the
      archive filename (e.g. `0.2.0` or `0.1.0+g9f3a2b.dirty`).
- [ ] `ga` CLI runs (e.g. `ga status` or `ga list`) without a "command not found".
- [ ] Uninstall removes: the install dir, the `ga` PATH entry / symlink, and all
      shortcuts/menu entries. No leftover files under the install path.
- [ ] After uninstall, `mykey.jsonc` / `memory/` / `.ga_data/` are either
      preserved (Win — re-installable) or gone with the app (mac/linux) as designed.

## Windows (`.exe`, Inno Setup)

- [ ] Default install is **per-user** to `%LOCALAPPDATA%\Programs\GenericAgent`,
      **no UAC prompt** appears.
- [ ] Start-menu shortcut "GenericAgent" present; desktop shortcut appears only
      if the "desktop icon" task was ticked.
- [ ] Shortcut launches via `pythonw.exe` (no console window flashes).
- [ ] Shortcut icon is the branded `ga.ico` (not a generic Python/exe icon).
- [ ] Install dir is on **user PATH**; `ga` works in cmd / PowerShell / Git Bash.
- [ ] `/ALLUSERS=1` (run installer from an elevated prompt with that flag) installs
      to `Program Files`, **triggers UAC**, and puts the dir on **system PATH**.
- [ ] Custom destination folder: typing a different path in the wizard works and
      the app launches from there.
- [ ] Uninstall (Add/Remove Programs) cleans PATH + shortcuts + files.

## macOS (`.pkg`)

- [ ] `.pkg` install prompts for admin password (mac standard).
- [ ] `GenericAgent.app` appears in `/Applications` and in Launchpad with the
      branded icon.
- [ ] **Unsigned first launch**: right-click → Open → Open anyway works (no
      quarantine block). Document `xattr -d com.apple.quarantine` as fallback.
- [ ] Double-click from Dock / Finder opens the GUI window.
- [ ] `/usr/local/bin/ga` exists and `ga --version` works in Terminal.
- [ ] Moving `GenericAgent.app` to another folder: GUI still launches (paths are
      relative); the `ga` CLI symlink breaks (expected — note in README).
- [ ] `sudo /Applications/GenericAgent.app/Contents/Resources/uninstall.sh`
      removes the app + the `/usr/local/bin/ga` symlink.
- [ ] Arch matches: `mac-arm64` build installs/launches on Apple Silicon;
      `mac-x64` build does likewise on Intel (or via Rosetta).

## Linux (`.deb` + `.rpm`)

- [ ] `apt install ./GenericAgent-<ver>-linux-x64.deb` pulls in
      `libwebkit2gtk-4.1-0` + `gir1.2-gtk-3.0` if missing.
- [ ] `dnf install ./GenericAgent-<ver>-linux-x64.rpm` pulls in `webkit2gtk4.1`
      + `gtk3` if missing.
- [ ] App menu entry "GenericAgent" appears with the branded icon.
- [ ] `/opt/GenericAgent/` contains `python/`, `launch.pyw`, `ga-cli.sh`, `ga.png`.
- [ ] `/usr/local/bin/ga` → `/opt/GenericAgent/ga-cli.sh` (symlink); `ga` works.
- [ ] GUI launches from the menu (`Exec=/opt/GenericAgent/run.sh`).
- [ ] `apt remove genericagent` / `dnf remove genericagent` cleans `/opt`,
      the `.desktop`, and the `ga` symlink; desktop database updated.

## Sign-off

- Tester: _______________  Date: __________
- Arch(es) tested: ____________________________________________
- Version: __________  (archive filename: ______________________)
- Notes / known issues: _______________________________________
