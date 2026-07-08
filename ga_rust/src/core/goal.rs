use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

/// Goal lifecycle states per spec:
/// proposed → confirmed → running → done | failed | budget_exhausted | timeout
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalState {
    Proposed,
    Confirmed,
    Running,
    Paused,
    Done,
    Failed,
    BudgetExhausted,
    Timeout,
}

impl GoalState {
    pub fn as_str(&self) -> &'static str {
        match self {
            GoalState::Proposed => "proposed",
            GoalState::Confirmed => "confirmed",
            GoalState::Running => "running",
            GoalState::Paused => "paused",
            GoalState::Done => "done",
            GoalState::Failed => "failed",
            GoalState::BudgetExhausted => "budget_exhausted",
            GoalState::Timeout => "timeout",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "proposed" => Some(GoalState::Proposed),
            "confirmed" => Some(GoalState::Confirmed),
            "running" => Some(GoalState::Running),
            "paused" => Some(GoalState::Paused),
            "done" => Some(GoalState::Done),
            "failed" => Some(GoalState::Failed),
            "budget_exhausted" => Some(GoalState::BudgetExhausted),
            "timeout" => Some(GoalState::Timeout),
            _ => None,
        }
    }

    pub fn is_terminal(&self) -> bool {
        matches!(self, GoalState::Done | GoalState::Failed | GoalState::BudgetExhausted | GoalState::Timeout)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Goal {
    pub id: String,
    pub title: String,
    pub description: Option<String>,
    pub state: GoalState,
    pub priority: i64,
    pub max_runners: Option<i64>,
    pub deliverable: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SubGoalStatus {
    Pending,
    InProgress,
    Done,
    Failed,
}

impl SubGoalStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            SubGoalStatus::Pending => "pending",
            SubGoalStatus::InProgress => "in_progress",
            SubGoalStatus::Done => "done",
            SubGoalStatus::Failed => "failed",
        }
    }

    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "pending" => Some(SubGoalStatus::Pending),
            "in_progress" => Some(SubGoalStatus::InProgress),
            "done" => Some(SubGoalStatus::Done),
            "failed" => Some(SubGoalStatus::Failed),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SubGoal {
    pub id: String,
    pub goal_id: String,
    pub session_id: Option<String>,
    pub title: String,
    pub status: SubGoalStatus,
    pub created_at: String,
}

/// Static validation for goal state transitions.
pub struct GoalController;

impl GoalController {
    /// Valid transitions per spec.
    pub fn validate_transition(from: &GoalState, to: &GoalState) -> Result<()> {
        let valid = match (from, to) {
            (GoalState::Proposed, GoalState::Confirmed) => true,
            (GoalState::Proposed, GoalState::Failed) => true,
            (GoalState::Confirmed, GoalState::Running) => true,
            (GoalState::Confirmed, GoalState::Failed) => true,
            (GoalState::Running, GoalState::Paused) => true,
            (GoalState::Running, GoalState::Done) => true,
            (GoalState::Running, GoalState::Failed) => true,
            (GoalState::Running, GoalState::BudgetExhausted) => true,
            (GoalState::Running, GoalState::Timeout) => true,
            (GoalState::Paused, GoalState::Running) => true,
            (GoalState::Paused, GoalState::Failed) => true,
            // Terminal states: no transitions out
            _ => false,
        };
        if valid {
            Ok(())
        } else {
            bail!(
                "Invalid goal state transition: {} → {}",
                from.as_str(),
                to.as_str()
            )
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_transitions() {
        assert!(GoalController::validate_transition(
            &GoalState::Proposed,
            &GoalState::Confirmed
        )
        .is_ok());
        assert!(GoalController::validate_transition(
            &GoalState::Confirmed,
            &GoalState::Running
        )
        .is_ok());
        assert!(GoalController::validate_transition(
            &GoalState::Running,
            &GoalState::Paused
        )
        .is_ok());
        assert!(GoalController::validate_transition(
            &GoalState::Paused,
            &GoalState::Running
        )
        .is_ok());
        assert!(GoalController::validate_transition(
            &GoalState::Running,
            &GoalState::Done
        )
        .is_ok());
    }

    #[test]
    fn test_invalid_transitions() {
        assert!(GoalController::validate_transition(
            &GoalState::Done,
            &GoalState::Running
        )
        .is_err());
        assert!(GoalController::validate_transition(
            &GoalState::Failed,
            &GoalState::Running
        )
        .is_err());
        assert!(GoalController::validate_transition(
            &GoalState::Proposed,
            &GoalState::Running
        )
        .is_err());
    }

    #[test]
    fn test_terminal_states() {
        assert!(GoalState::Done.is_terminal());
        assert!(GoalState::Failed.is_terminal());
        assert!(GoalState::BudgetExhausted.is_terminal());
        assert!(GoalState::Timeout.is_terminal());
        assert!(!GoalState::Running.is_terminal());
        assert!(!GoalState::Paused.is_terminal());
    }
}
