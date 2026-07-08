## ADDED Requirements

### Requirement: Stable Command Surface

The `ga` CLI SHALL expose a stable command surface for Supervisor Agents: `status`, `sessions list`, `session new`, `session watch`, `session archive`, `project create`, `project follow`, `llm set`. `session new` SHALL accept `--runner=<kind>` (default `ga`). Every write command SHALL require an origin triple (`via=supervisor`, `supervisor=<id>`, `reason=<text>`).

#### Scenario: Supervisor dispatches a session with a runner

- **WHEN** `ga session new --runner=codex --project=<p> --supervisor=<id> --reason=<text> "<task>"` is run
- **THEN** Core creates a session, spawns the chosen runner, and the origin triple is recorded and shown in the timeline

#### Scenario: Watch a session event stream

- **WHEN** `ga session watch <id>` is run
- **THEN** Core streams the session's events to stdout until the session ends or the client disconnects

#### Scenario: Follow a project until idle

- **WHEN** `ga project follow <p> --until-idle` is run
- **THEN** Core aggregates member session events and exits when all member sessions are idle

#### Scenario: Missing origin triple on a write command

- **WHEN** a write command is invoked without `--supervisor` or `--reason`
- **THEN** the CLI exits with a usage error code and does not perform the write

#### Scenario: Unknown runner kind

- **WHEN** `session new` is invoked with a `--runner` value not in the adapter registry
- **THEN** the CLI exits with a usage error code and does not spawn

### Requirement: Machine-Readable JSON Output

Every command SHALL support `--json` emitting a versioned, schema-stable JSON document. The JSON SHALL include a `schema_version` field.

#### Scenario: JSON output is schema-stable

- **WHEN** `ga status --json` is run across versions within the same major
- **THEN** the JSON field names and structure remain backward-compatible

### Requirement: Stable Exit Codes

The CLI SHALL use fixed exit code semantics: 0 success, 2 usage error, 3 not found, 4 conflict, 5 rate-limited, and 10+ for domain-specific business errors.

#### Scenario: Not-found exit code

- **WHEN** a command references a non-existent session id
- **THEN** the CLI exits with code 3 and an error message
