## ADDED Requirements

### Requirement: Two-Level Approval Policy

Core SHALL enforce approval governance at two levels: (1) tool-level for runners emitting structured `tool_call` events (per-call / allowlist / YOLO per session and project); (2) command-level for stream runners (pre-run confirm / YOLO), since they expose no structured tool events.

#### Scenario: Structured runner tool-level approval

- **WHEN** a GA runner attempts a risky tool call and the session policy is per-call approval
- **THEN** Core suspends the tool call and requests a decision from the bound client; the call only proceeds after approval

#### Scenario: Stream runner command-level confirm

- **WHEN** a stream runner session is not YOLO and a new run is dispatched
- **THEN** Core requests a pre-run confirm from the bound client before spawning the runner

#### Scenario: YOLO auto-approves all

- **WHEN** a session is in YOLO mode
- **THEN** all tool calls and runs are auto-approved and the policy is recorded on each event

### Requirement: Approval via CLI

The CLI SHALL allow approving or rejecting pending approvals interactively, or non-interactively via `--approve` / `--reject` with an approval id, carrying the origin triple.

#### Scenario: Non-interactive approval

- **WHEN** `ga approval <id> --approve --supervisor=<id> --reason=<text>` is run
- **THEN** Core resolves the pending approval and the suspended tool call or run proceeds or is rejected
