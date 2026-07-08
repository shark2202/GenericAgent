# skill-memory-integration Specification

## Purpose
TBD - created by archiving change integrate-skills-with-memory. Update Purpose after archive.
## Requirements
### Requirement: Skill as L3 Subcategory with L1 Routing

Skill SHALL be treated as an independent L3 subcategory, distinct from Agent-authored SOPs. L1 index (`global_mem_insight.txt`) SHALL include a `[Skills]` section with structured multi-line entries, one per skill, containing the skill name, SKILL.md absolute path, and optional experience file path.

#### Scenario: L1 Skills section format
- **WHEN** skills are discovered and synced to L1
- **THEN** L1 contains a `[Skills]` section where each line follows the format: `<name>: <SKILL.md_path> | <experience_file_path>` (experience file path omitted if no experience file exists)

#### Scenario: Agent discovers skill via L1
- **WHEN** the agent reads L1 and finds a skill entry
- **THEN** the agent can `file_read` the SKILL.md path to load the full skill content

#### Scenario: Skill entry with experience file
- **WHEN** a skill has a corresponding `memory/skill_exp_<name>.md` file
- **THEN** the L1 entry includes both the SKILL.md path and the experience file path, separated by ` | `

#### Scenario: Skill entry without experience file
- **WHEN** a skill has no corresponding experience file
- **THEN** the L1 entry includes only the SKILL.md path, with no trailing ` | `

### Requirement: Skill Experience Files

The system SHALL support skill experience files at `memory/skill_exp_<name>.md` as L3 knowledge. These files are written by the agent through the standard self-evolution mechanism (`do_start_long_term_update` + `file_patch`), recording action-validated learnings from using a skill.

#### Scenario: Agent writes skill experience after usage
- **WHEN** the agent uses a skill and determines there are valuable learnings to record
- **THEN** the agent calls `start_long_term_update`, follows L0 rules, and writes experience to `memory/skill_exp_<name>.md`

#### Scenario: Experience file influences future tasks
- **WHEN** a future task is similar to a previous task that used a skill
- **THEN** L1 routes the agent to both the SKILL.md and the experience file, allowing the agent to read past learnings before executing

#### Scenario: Experience file naming convention
- **WHEN** a skill named `comet-open` has experience recorded
- **THEN** the experience file is named `memory/skill_exp_comet-open.md`

### Requirement: Skill Usage Tracking in Working Memory

The system SHALL track skill usage in the working memory `related_sop` field, enabling cross-task inheritance through the existing `agentmain.py:159-163` mechanism.

#### Scenario: Skill usage recorded in related_sop
- **WHEN** the agent uses a skill during a task
- **THEN** the skill path is recorded in `working['related_sop']`

#### Scenario: Skill usage inherited across tasks
- **WHEN** a new task starts and the previous handler had skill paths in `related_sop`
- **THEN** the new handler inherits the `key_info` containing skill usage context, following the existing cross-task inheritance mechanism

### Requirement: L1 Auto-Sync with Manual Override

The system SHALL auto-sync discovered skills to the L1 `[Skills]` section on startup and on directory change. Auto-synced entries SHALL be marked to distinguish from agent-manual annotations. The agent MAY add manual annotations (e.g., usage notes) to L1 skill entries through self-evolution; auto-sync SHALL preserve manual annotations.

#### Scenario: New skill auto-synced to L1
- **WHEN** a new SKILL.md is added to a scanned directory and the agent starts or `get_system_prompt()` is called
- **THEN** the new skill appears in the L1 `[Skills]` section on the next sync

#### Scenario: Removed skill auto-removed from L1
- **WHEN** a SKILL.md is deleted from a scanned directory
- **THEN** on the next sync, the corresponding entry is removed from the L1 `[Skills]` auto-synced section

#### Scenario: Manual annotations preserved during sync
- **WHEN** the agent has added manual annotations to a skill entry in L1, and auto-sync runs
- **THEN** the manual annotations are preserved; only the auto-synced portion (path, name) is updated

### Requirement: Optional Skill Version Backup

The system SHALL support an opt-in mechanism for the agent to fix factual errors in SKILL.md by backing up the original version before patching. This mechanism requires explicit user confirmation before execution.

#### Scenario: Agent discovers factual error in SKILL.md
- **WHEN** the agent identifies a factual error in a SKILL.md and the opt-in mode is enabled
- **THEN** the system backs up the original SKILL.md to `SKILL.md.bak` (or versioned backup) before applying the patch

#### Scenario: Opt-in mode disabled by default
- **WHEN** the opt-in mode is not explicitly enabled
- **THEN** the agent writes experience notes to `memory/skill_exp_<name>.md` instead of modifying SKILL.md directly

