## MODIFIED Requirements

### Requirement: Skill Catalog Discovery

The system SHALL scan `$HOME/.agents/skills/` and `$CWD/.agents/skills/` for directories containing a `SKILL.md` file, parse their YAML frontmatter, and build a catalog of available skills. The discovered skills SHALL be synced to the L1 index `[Skills]` section instead of being formatted as a catalog string for system prompt injection.

#### Scenario: Skills directory exists with valid skills
- **WHEN** `$HOME/.agents/skills/` contains directories with `SKILL.md` files that have valid YAML frontmatter with `name` field
- **THEN** each valid skill is synced to the L1 `[Skills]` section with its name, description, and absolute path

#### Scenario: Skills directory does not exist
- **WHEN** `$HOME/.agents/skills/` or `$CWD/.agents/skills/` does not exist
- **THEN** the directory is silently skipped, no error is raised

#### Scenario: SKILL.md without valid frontmatter
- **WHEN** a `SKILL.md` file has no YAML frontmatter or no `name` field
- **THEN** the skill is silently excluded from the L1 sync

#### Scenario: Non-SKILL.md files in skill directories
- **WHEN** a skill directory contains files other than `SKILL.md`
- **THEN** those files are ignored during discovery

#### Scenario: Directory name differs from frontmatter name
- **WHEN** a skill directory is named `foo` but its `SKILL.md` frontmatter has `name: bar`
- **THEN** the L1 entry SHALL use `bar` as the skill identifier, and the directory name `foo` SHALL only be used for conflict resolution (project-over-user override)

### Requirement: Skill Catalog Injection into System Prompt

The system SHALL NOT append a skill catalog section to the system prompt. Skill discovery results are routed exclusively through the L1 index `[Skills]` section, which is injected into the system prompt via `get_global_memory()`.

#### Scenario: No skill catalog appended to system prompt
- **WHEN** `get_system_prompt()` is called
- **THEN** no `## Available Skills` section is appended; skill routing is handled by L1 `[Skills]` section within `get_global_memory()` output

### Requirement: Agent On-Demand Skill Loading

The agent SHALL be able to read the full content of a skill's `SKILL.md` file using the existing `file_read` tool, using the path provided in the L1 `[Skills]` section.

#### Scenario: Agent loads a skill by path from L1
- **WHEN** the agent reads L1, finds a skill entry, and invokes `file_read` with the SKILL.md path
- **THEN** the full `SKILL.md` content is returned, including frontmatter and body

#### Scenario: Agent follows skill instructions
- **WHEN** the agent reads a `SKILL.md` that contains actionable instructions
- **THEN** the agent processes the markdown and executes the instructions using its available tools

#### Scenario: Agent reads skill experience file
- **WHEN** the L1 skill entry includes an experience file path and the agent invokes `file_read` with that path
- **THEN** the experience file content is returned, containing past learnings about using this skill

### Requirement: Hot Reloading

The system SHALL re-scan skill directories on every `get_system_prompt()` call and sync changes to the L1 `[Skills]` section, so that adding or removing skills takes effect without restarting the agent.

#### Scenario: New skill added at runtime
- **WHEN** a new `SKILL.md` is added to a scanned directory while the agent is running
- **THEN** on the next `get_system_prompt()` call, the new skill is synced to the L1 `[Skills]` section

#### Scenario: Skill removed at runtime
- **WHEN** a previously-available `SKILL.md` is deleted while the agent is running
- **THEN** on the next `get_system_prompt()` call, the skill entry is removed from the L1 `[Skills]` auto-synced section

### Requirement: Skill Priority (Project over User)

The system SHALL resolve name conflicts between project-level (`$CWD/.agents/skills/`) and user-level (`$HOME/.agents/skills/`) skills by preferring the project-level version. The L1 `[Skills]` section SHALL reflect the resolved path.

#### Scenario: Same skill name in both paths
- **WHEN** `$CWD/.agents/skills/foo/SKILL.md` and `$HOME/.agents/skills/foo/SKILL.md` both exist with name "foo"
- **THEN** the L1 `[Skills]` section includes only the project-level version with its path to `$CWD/.agents/skills/foo/SKILL.md`

#### Scenario: Different skill names in both paths
- **WHEN** project and user paths contain skills with different names
- **THEN** all skills are included in the L1 `[Skills]` section
