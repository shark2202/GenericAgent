## Tasks

### 1. Build script — package-selfextract.sh

- [ ] 1.1 Create `scripts/package-selfextract.sh` skeleton: arg `<arch>`, source `common.sh`, `ga_version`, arch→os guard via `ga_assert_host_can_build`, `ga_require tar sha256sum awk sed`
- [ ] 1.2 Build payload: tar.gz of assemble-produced `GenericAgent/` tree (GNU `--transform` / BSD symlink fallback), compute sha256
- [ ] 1.3 Assemble self-extracting file: shebang + `PAYLOAD_SHA256`/`PAYLOAD_TAG` inject + header heredoc (`exit 0` before marker) + `__PAYLOAD_BELOW__` marker + `cat payload` append; `chmod +x`; output `dist/release/GenericAgent-<ver>-<arch>.install.sh`

### 2. Installer runtime header

- [ ] 2.1 Arg parsing: `--install-dir`/`--bin-dir`/`--check`/`--version`/`--uninstall`/`-h`; defaults `~/.local/share/GenericAgent` + `~/.local/bin`
- [ ] 2.2 `--check`: awk locate marker → tail extract → sha256 verify → print OK/fail, no install
- [ ] 2.3 Install flow: locate payload → tail extract → sha256 verify → idempotent `rm -rf` old install dir → `tar -xzf` → move tree into place
- [ ] 2.4 Shebang fix: `grep -Il '^#!'` `python/bin/*`, `sed -i` rewrite to install path (GNU/BSD `sed` branch)
- [ ] 2.5 Symlink: `ln -sf <install-dir>/ga.sh <bin>/ga`
- [ ] 2.6 Linux `.desktop`: write `~/.local/share/applications/genericagent.desktop` (Exec/Icon), `update-desktop-database`; skip on macOS
- [ ] 2.7 webkit detection (Linux): `ldconfig -p | grep libwebkit2gtk-4.1`; missing → print `apt install libwebkit2gtk-4.1-0` (or dnf equiv) hint + continue
- [ ] 2.8 `--uninstall`: `rm -rf` install dir + unlink `ga` symlink + rm `.desktop` (linux)
- [ ] 2.9 `--version`: print base version + payload sha256 first 16 chars

### 3. release-local.sh integration

- [ ] 3.1 Insert selfextract step between assemble and build-installer in `release-local.sh`
- [ ] 3.2 `--dry-run` stops after assemble (no selfextract / build-installer)
- [ ] 3.3 Verify SRC_EXT routing still correct for all arches

### 4. Verify

- [ ] 4.1 `bash -n` on `package-selfextract.sh` + `release-local.sh`
- [ ] 4.2 linux-x64: `release-local.sh linux-x64 --no-bundle` → `.install.sh` produced alongside `.deb`/`.rpm`
- [ ] 4.3 `./x.install.sh --check` passes; tampered payload → fails
- [ ] 4.4 `./x.install.sh` → installs to `~/.local/share/GenericAgent`, `ga --version` works, `.desktop` created (linux)
- [ ] 4.5 webkit-missing scenario: hint printed, CLI still works (mock by checking hint path only)
- [ ] 4.6 `--uninstall` removes install dir + symlink + `.desktop`
- [ ] 4.7 `shellcheck scripts/package-selfextract.sh` (if available)
- [ ] 4.8 `ruff check .` / `pytest tests/` unaffected (no Python source touched)
