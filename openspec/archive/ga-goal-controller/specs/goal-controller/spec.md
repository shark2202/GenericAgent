## ADDED Requirements

### Requirement: Goal Lifecycle State Machine

Core SHALL manage goal state with transitions: `proposed → confirmed → running → done | failed | budget-exhausted | timeout`. A goal SHALL persist state to the SQLite `goals` table and survive Core restart.

#### Scenario: Propose creates a pending goal

- **WHEN** `ga goal propose "<goal>" --supervisor=<id> --reason=<text>` is run
- **THEN** Core creates a goal in `proposed` state, returns a goal id and a one-time confirm-token, and does NOT start execution

#### Scenario: Confirm-token required to run

- **WHEN** `ga goal run --proposal=<id>` is run without a valid confirm-token
- **THEN** Core rejects with an error and does not start the goal

#### Scenario: Goal survives restart

- **WHEN** Core restarts while a goal is `running`
- **THEN** Core restores the goal from SQLite and resumes the controller by reconnecting or restarting its sessions per policy

### Requirement: Subagent Budget Enforcement

A goal SHALL declare `max_concurrent_runners`. The controller SHALL NOT spawn new runners when the active count reaches the budget; when no progress is possible (budget full and no active runner), the goal SHALL transition to `budget-exhausted`.

#### Scenario: Budget caps concurrency

- **WHEN** a goal with budget 3 has 3 active runners and a new subtask is ready
- **THEN** the controller does not spawn a 4th runner until one becomes idle

#### Scenario: Budget exhausted

- **WHEN** a goal has pending subtasks but the budget is full and no active runner can proceed
- **THEN** the goal transitions to `budget-exhausted` and stops dispatching

### Requirement: Duration Timeout

A goal SHALL support `max_duration`. When elapsed runtime reaches it, the goal SHALL transition to `timeout` and gracefully terminate active runners.

#### Scenario: Duration expiry

- **WHEN** a goal's elapsed runtime reaches its `max_duration`
- **THEN** the goal transitions to `timeout` and active runners are gracefully terminated

### Requirement: Background Execution and Reattach

A goal SHALL run in the Core daemon, independent of the originating client. Clients SHALL be able to query `ga goal status <id>` and `ga goal deliverable get <id>` from any terminal.

#### Scenario: Query running goal from another terminal

- **WHEN** a goal is `running` and a client runs `ga goal status <id>` from a new terminal
- **THEN** Core returns the current state, progress, and budget usage

### Requirement: Deliverable Retrieval

Core SHALL aggregate a goal's deliverable from its sessions' final answers and key events, and SHALL expose it via `ga goal deliverable get <id>`.

#### Scenario: Retrieve deliverable after completion

- **WHEN** a goal has reached a terminal state and `ga goal deliverable get <id>` is run
- **THEN** Core returns the aggregated deliverable containing final answers and a summary
