## ADDED Requirements

### Requirement: Skill Catalog Discovery

The system SHALL scan `$HOME/.agents/skills/` and `$CWD/.agents/skills/` for directories containing a `SKILL.md` file, parse their YAML frontmatter, and build a catalog of available skills.

#### Scenario: Skills directory exists with valid skills
- **WHEN** `$HOME/.agents/skills/` contains directories with `SKILL.md` files that have valid YAML frontmatter with `name` field
- **THEN** each valid skill is included in the catalog with its name, description, and absolute path

#### Scenario: Skills directory does not exist
- **WHEN** `$HOME/.agents/skills/` or `$CWD/.agents/skills/` does not exist
- **THEN** the directory is silently skipped, no error is raised

#### Scenario: SKILL.md without valid frontmatter
- **WHEN** a `SKILL.md` file has no YAML frontmatter or no `name` field
- **THEN** the skill is silently excluded from the catalog

#### Scenario: Non-SKILL.md files in skill directories
- **WHEN** a skill directory contains files other than `SKILL.md`
- **THEN** those files are ignored during catalog construction

#### Scenario: Directory name differs from frontmatter name
- **WHEN** a skill directory is named `foo` but its `SKILL.md` frontmatter has `name: bar`
- **THEN** the catalog SHALL use `bar` as the skill identifier, and the directory name `foo` SHALL only be used for conflict resolution (project-over-user override)

### Requirement: Skill Catalog Injection into System Prompt

The system SHALL append the skill catalog to the system prompt in a compact format, listing each skill's name, description, and file path.

#### Scenario: Catalog with multiple skills
- **WHEN** 3 skills are discovered
- **THEN** the system prompt ends with a section listing all 3 skills, each on one line: `- **name**: description (path/to/SKILL.md)`

#### Scenario: Catalog with no skills
- **WHEN** no valid skills are found in any scan path
- **THEN** no skill catalog section is appended to the system prompt

### Requirement: Agent On-Demand Skill Loading

The agent SHALL be able to read the full content of a skill's `SKILL.md` file using the existing `file_read` tool, using the path provided in the catalog.

#### Scenario: Agent loads a skill by path
- **WHEN** the agent invokes `file_read` with the path from the catalog
- **THEN** the full `SKILL.md` content is returned, including frontmatter and body

#### Scenario: Agent follows skill instructions
- **WHEN** the agent reads a `SKILL.md` that contains actionable instructions
- **THEN** the agent processes the markdown and executes the instructions using its available tools

### Requirement: Hot Reloading

The system SHALL re-scan skill directories on every `get_system_prompt()` call, so that adding or removing skills takes effect without restarting the agent.

#### Scenario: New skill added at runtime
- **WHEN** a new `SKILL.md` is added to a scanned directory while the agent is running
- **THEN** on the next `get_system_prompt()` call, the new skill appears in the catalog

#### Scenario: Skill removed at runtime
- **WHEN** a previously-available `SKILL.md` is deleted while the agent is running
- **THEN** on the next `get_system_prompt()` call, the skill is no longer in the catalog

### Requirement: Skill Priority (Project over User)

The system SHALL resolve name conflicts between project-level (`$CWD/.agents/skills/`) and user-level (`$HOME/.agents/skills/`) skills by preferring the project-level version.

#### Scenario: Same skill name in both paths
- **WHEN** `$CWD/.agents/skills/foo/SKILL.md` and `$HOME/.agents/skills/foo/SKILL.md` both exist with name "foo"
- **THEN** the catalog includes only the project-level version with its path to `$CWD/.agents/skills/foo/SKILL.md`

#### Scenario: Different skill names in both paths
- **WHEN** project and user paths contain skills with different names
- **THEN** all skills are included in the catalog, project-level listed first
