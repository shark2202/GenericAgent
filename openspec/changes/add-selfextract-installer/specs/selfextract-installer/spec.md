# Self-Extracting Installer

## ADDED Requirements

### Requirement: Produce single-file self-extracting installer

`scripts/package-selfextract.sh` SHALL produce a single executable file `GenericAgent-<version>-<arch>.install.sh` containing a bash header, a `__PAYLOAD_BELOW__` marker line, and the assemble-produced `GenericAgent/` tree appended as a tar.gz payload. The header `exit 0` SHALL appear before the marker so payload bytes are never parsed by the shell.

#### Scenario: Build produces single .install.sh file

- **WHEN** `package-selfextract.sh linux-x64` runs after assemble has produced the `GenericAgent/` tree
- **THEN** a single file `dist/release/GenericAgent-<version>-linux-x64.install.sh` exists, is executable, and its size equals header + marker + payload

#### Scenario: Payload is the whole GenericAgent tree

- **WHEN** the installer is extracted
- **THEN** the payload contains `python/`, the GA source tree, `launch.pyw`, `run.sh`, `ga.sh`, `run.cmd`, `ga.cmd` (the assemble output)

### Requirement: Payload integrity verification

The installer SHALL embed the payload sha256 in its header and verify it after extraction. `--check` SHALL verify without installing. A mismatch SHALL abort with non-zero exit.

#### Scenario: --check passes on intact file

- **WHEN** the user runs `./GenericAgent-<version>-<arch>.install.sh --check`
- **THEN** the system computes the payload sha256, compares to the embedded value, and prints OK without installing

#### Scenario: Tampered payload fails

- **WHEN** the payload bytes are modified after build
- **THEN** both `--check` and install SHALL detect the sha256 mismatch and exit non-zero with the expected vs actual hashes

#### Scenario: Corrupted download fails

- **WHEN** the file is truncated or bit-flipped during download
- **THEN** install SHALL abort with a sha256 mismatch before touching the install directory

### Requirement: Zero-privilege installation

The installer SHALL install to `~/.local/share/GenericAgent` by default and symlink `~/.local/bin/ga` → `<install-dir>/ga-cli.sh`, requiring no sudo. `--install-dir` and `--bin-dir` SHALL override the defaults.

#### Scenario: Default install without sudo

- **WHEN** a non-root user runs `./GenericAgent-<version>-linux-x64.install.sh` with no flags
- **THEN** the tree is extracted to `~/.local/share/GenericAgent`, `~/.local/bin/ga` is symlinked, and no privilege escalation is requested

#### Scenario: Custom install and bin directories

- **WHEN** the user runs `./x.install.sh --install-dir /opt/ga-user --bin-dir ~/bin`
- **THEN** the tree is installed to `/opt/ga-user` and `~/bin/ga` is symlinked

### Requirement: Idempotent reinstall

The installer SHALL remove the prior install directory before extracting, so re-running over an existing install leaves no stale files.

#### Scenario: Reinstall over prior install

- **WHEN** the install directory already exists from a prior run and the user runs the installer again
- **THEN** the old directory is removed and replaced with the fresh tree, with no leftover stale files

### Requirement: Console script shebang correction

The installer SHALL rewrite shebangs in `python/bin/*` console scripts from the build-time absolute path to the install-time path, so they work after relocation.

#### Scenario: Shebang points to install path after install

- **WHEN** the installer finishes extracting
- **THEN** every `#!`-script in `<install-dir>/python/bin/` has its shebang rewritten to `#!<install-dir>/python/bin/python3`

#### Scenario: Console script runs after relocation

- **WHEN** the user invokes a console script directly (not via the relative-path shim)
- **THEN** it executes using the installed python, not a stale build path

### Requirement: Linux desktop entry

On Linux, the installer SHALL create `~/.local/share/applications/genericagent.desktop` with `Exec=<install-dir>/run.sh` and `Icon=<install-dir>/ga.png`, and run `update-desktop-database` so the app appears in the application menu.

#### Scenario: Desktop entry created on Linux

- **WHEN** the installer runs on Linux
- **THEN** `~/.local/share/applications/genericagent.desktop` exists with the correct Exec and Icon paths, and `update-desktop-database` is invoked

#### Scenario: No desktop entry on macOS

- **WHEN** the installer runs on macOS
- **THEN** no `.desktop` file is created (mac GUI users use the `.pkg`)

### Requirement: webkit missing detection (Linux)

On Linux, the installer SHALL detect whether `libwebkit2gtk-4.1` is available. If missing, it SHALL print an install hint (`apt install libwebkit2gtk-4.1-0` or distro equivalent) and continue installing rather than aborting.

#### Scenario: webkit present — silent install

- **WHEN** the installer runs on Linux with `libwebkit2gtk-4.1` installed
- **THEN** no webkit warning is printed and installation proceeds normally

#### Scenario: webkit missing — hint printed, install continues

- **WHEN** the installer runs on Linux without `libwebkit2gtk-4.1`
- **THEN** a message prints the `apt install` hint, installation completes, and `ga --version` works (CLI/TUI usable); GUI launch will require the user to install webkit separately

### Requirement: Uninstall

The installer SHALL support `--uninstall`, which removes the install directory, the `ga` symlink, and (on Linux) the `.desktop` entry.

#### Scenario: Uninstall removes all artifacts

- **WHEN** the user runs `./x.install.sh --uninstall` after a prior install
- **THEN** the install directory, `~/.local/bin/ga` symlink, and `.desktop` file are removed, leaving no GA artifacts

### Requirement: Version display

`--version` SHALL display the base version and the first 16 characters of the payload sha256. The output filename SHALL include the full version (with dev suffix when off-tag).

#### Scenario: --version output

- **WHEN** the user runs `./x.install.sh --version`
- **THEN** the output shows the base version and `payload sha256: <first-16>...`

### Requirement: release-local.sh integration

`release-local.sh` SHALL run `package-selfextract.sh` after assemble and before `build-installer.sh`. `--dry-run` SHALL stop after assemble without producing the `.install.sh`.

#### Scenario: Full release-local produces .install.sh

- **WHEN** the user runs `release-local.sh linux-x64`
- **THEN** the flow is bundle → assemble → selfextract → build-installer, and `dist/release/` contains the `.install.sh` alongside the `.tar.gz`/`.deb`/`.rpm`

#### Scenario: --dry-run skips selfextract and build-installer

- **WHEN** the user runs `release-local.sh linux-x64 --dry-run`
- **THEN** only the `.tar.gz` is produced; no `.install.sh` or native installer is built
