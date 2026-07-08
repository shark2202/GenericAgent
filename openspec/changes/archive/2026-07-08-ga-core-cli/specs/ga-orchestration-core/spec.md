## ADDED Requirements

### Requirement: Pluggable Runner Abstraction

Core SHALL drive agents through a pluggable `Runner` trait and SHALL NOT bind to any specific agent. A `runner_kind` (e.g. `ga`, `opencode`, `claude-code`, `codex`) SHALL be selectable per session via `--runner`. Core SHALL ship at least: a GA adapter emitting structured `tool_call` events, and a generic PTY/stream adapter for agents producing text output and status.

#### Scenario: Spawn a non-GA runner

- **WHEN** `session new --runner=codex "<task>"` is run
- **THEN** Core spawns the codex runner via the generic adapter and records stream events (output lines, status) to the session timeline

#### Scenario: Spawn a GA runner with structured events

- **WHEN** `session new --runner=ga "<task>"` is run
- **THEN** Core spawns the GA adapter and records structured `tool_call` events (args/result/timing) to the timeline

#### Scenario: Heterogeneous concurrent runners

- **WHEN** multiple sessions run with different runner kinds
- **THEN** each runs independently and a failure in one does not crash others or Core

### Requirement: Session Lifecycle Ownership

Core SHALL be the single authority for session state. Each session SHALL bind to a project (or default project), a working directory, an environment, and a Runner instance of a chosen kind. Core SHALL persist session state to SQLite and survive process restart.

#### Scenario: Create session bound to a project

- **WHEN** a session is created with `--project=<p>` and a working directory
- **THEN** Core assigns a session id, persists it to SQLite, and spawns the chosen Runner bound to that cwd/env

#### Scenario: Session survives Core restart

- **WHEN** Core daemon restarts after a session was active
- **THEN** the session record is restored from SQLite and the runner is reattachable or restarted per policy

#### Scenario: Concurrent sessions are isolated

- **WHEN** multiple sessions run concurrently
- **THEN** each runs in its own Runner process with independent cwd/env, and a failure in one does not crash others or Core

### Requirement: Project Workspace

Core SHALL support project workspaces that group sessions. A project SHALL have a name, an optional root directory, and a list of member sessions.

#### Scenario: Create project and add sessions

- **WHEN** a project is created and sessions are added with `--project=<p>`
- **THEN** the sessions are listed under the project and `project follow` can aggregate their event streams

### Requirement: Event Timeline

Core SHALL capture an ordered event stream per session, persisted to SQLite: structured events (`tool_call` args/result/timing) for structured runners, and stream events (output line, status change) for stream runners. Events SHALL be retrievable per session and per project.

#### Scenario: Structured tool-call events are recorded

- **WHEN** a GA runner executes a tool call
- **THEN** Core records a structured event with tool name, args, result, and timestamp

#### Scenario: Stream events are recorded

- **WHEN** a stream runner emits an output line or status change
- **THEN** Core records a stream event with the line/status and timestamp

#### Scenario: Replay session event stream

- **WHEN** a client requests a session's events
- **THEN** Core returns the ordered event list

### Requirement: Cross-Platform Runner Process Management

Core SHALL manage Runner process lifecycles across Linux, macOS, and Windows. On Unix it SHALL use process groups for graceful termination; on Windows it SHALL use Job Objects so the runner subtree terminates with Core.

#### Scenario: Core exit terminates runners

- **WHEN** Core daemon exits
- **THEN** all runner processes are terminated (graceful then forced) on all supported platforms

### Requirement: Detach and Reattach

Core SHALL keep runners running when the originating client disconnects, and SHALL allow a client to reattach to an existing session from any terminal.

#### Scenario: Reattach after disconnect

- **WHEN** a client disconnects from a running session and a new client reattaches
- **THEN** the session continues and the new client receives subsequent events
