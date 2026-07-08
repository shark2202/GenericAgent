use std::collections::HashMap;

use crate::store::{Origin, RiskLevel};

/// Information about a call being evaluated by the approval policy.
#[derive(Debug, Clone)]
pub struct CallInfo {
    pub tool_name: Option<String>,
    pub command: Option<String>,
    pub session_id: Option<String>,
}

/// Decision returned by the approval policy's classify/evaluate methods.
#[derive(Debug, Clone)]
pub enum CallDecision {
    /// Auto-approved (Low risk or YOLO/allowlist).
    AutoApproved { risk: RiskLevel },
    /// Requires explicit approval — request created, awaiting resolution.
    RequiresApproval { risk: RiskLevel, description: Option<String> },
}

/// Rule for classifying a shell command by pattern.
#[derive(Debug, Clone)]
pub struct CommandRule {
    pub pattern: String,
    pub risk: RiskLevel,
}

/// The approval policy determines risk classification and blocking behavior.
#[derive(Debug, Clone)]
pub struct ApprovalPolicy {
    pub tool_rules: HashMap<String, RiskLevel>,
    pub command_rules: Vec<CommandRule>,
    /// YOLO mode: auto-approve everything (dangerous, for sandboxes only)
    pub yolo: bool,
    /// Explicit allowlist: tools/commands that bypass approval even at Medium/High
    pub allowlist: Vec<String>,
}

impl Default for ApprovalPolicy {
    fn default() -> Self {
        Self {
            tool_rules: default_tool_rules(),
            command_rules: default_command_rules(),
            yolo: false,
            allowlist: Vec::new(),
        }
    }
}

impl ApprovalPolicy {
    /// Classify a tool call by name. Returns the risk level.
    pub fn classify_tool(&self, tool_name: &str) -> RiskLevel {
        if self.yolo || self.allowlist.contains(&tool_name.to_string()) {
            return RiskLevel::Low;
        }
        self.tool_rules
            .get(tool_name)
            .cloned()
            .unwrap_or(RiskLevel::Medium) // Unknown tools default to Medium
    }

    /// Classify a shell command by matching against command rules.
    /// Returns the risk level of the first matching rule, or Medium for unknown.
    pub fn classify_command(&self, command: &str) -> RiskLevel {
        if self.yolo {
            return RiskLevel::Low;
        }
        // Check allowlist (exact command prefix match)
        for allowed in &self.allowlist {
            if command.starts_with(allowed) {
                return RiskLevel::Low;
            }
        }
        // Match against command rules (simple prefix/contains matching)
        for rule in &self.command_rules {
            if command.starts_with(&rule.pattern) || command.contains(&rule.pattern) {
                return rule.risk.clone();
            }
        }
        RiskLevel::Medium // Unknown commands default to Medium
    }

    /// Determine whether a call at the given risk level should block
    /// (require explicit approval) or auto-approve.
    pub fn should_block(&self, risk: &RiskLevel) -> bool {
        if self.yolo {
            return false;
        }
        match risk {
            RiskLevel::Low => false,     // Auto-approve
            RiskLevel::Medium => true,   // Prompt (block until decision)
            RiskLevel::High => true,     // Block (require explicit approval)
        }
    }

    /// Classify a call and return a decision.
    pub fn classify(&self, info: &CallInfo) -> CallDecision {
        let risk = if let Some(ref tool) = info.tool_name {
            self.classify_tool(tool)
        } else if let Some(ref cmd) = info.command {
            self.classify_command(cmd)
        } else {
            RiskLevel::Low
        };

        if self.should_block(&risk) {
            CallDecision::RequiresApproval {
                risk: risk.clone(),
                description: Some(format!("{:?} risk call", risk)),
            }
        } else {
            CallDecision::AutoApproved { risk: risk.clone() }
        }
    }

    /// Alias for classify() — used by orchestrator.
    pub fn evaluate(&self, info: &CallInfo) -> CallDecision {
        self.classify(info)
    }

    /// Create policy from config store values.
    /// Reads: approval.yolo (bool), approval.allowlist (JSON array of strings)
    pub fn from_config(
        yolo: bool,
        allowlist: Vec<String>,
        extra_tool_rules: HashMap<String, RiskLevel>,
    ) -> Self {
        let mut policy = Self::default();
        policy.yolo = yolo;
        policy.allowlist = allowlist;
        // Merge extra tool rules (user overrides)
        for (tool, risk) in extra_tool_rules {
            policy.tool_rules.insert(tool, risk);
        }
        policy
    }
}

/// Default tool risk classification per spec §8.2.
fn default_tool_rules() -> HashMap<String, RiskLevel> {
    let mut m = HashMap::new();
    // Read-only tools → Low
    m.insert("file_read".into(), RiskLevel::Low);
    m.insert("web_scan".into(), RiskLevel::Low);
    m.insert("web_execute_js".into(), RiskLevel::Low);
    m.insert("code_run".into(), RiskLevel::Low); // read-only execution
    m.insert("ask_user".into(), RiskLevel::Low);

    // Write tools → Medium
    m.insert("file_write".into(), RiskLevel::Medium);
    m.insert("file_patch".into(), RiskLevel::Medium);

    // Destructive tools → High
    m.insert("shell_exec".into(), RiskLevel::High);
    m.insert("process_kill".into(), RiskLevel::High);
    m.insert("network_bind".into(), RiskLevel::High);

    m
}

/// Default command risk classification per spec §8.2.
fn default_command_rules() -> Vec<CommandRule> {
    vec![
        // Read-only commands → Low
        CommandRule { pattern: "ls".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "cat".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "head".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "tail".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "grep".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "find".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "git status".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "git log".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "git diff".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "git show".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "echo".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "which".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "pwd".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "env".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "printenv".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "whoami".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "date".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "uname".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "ps".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "df".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "free".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "stat".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "file".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "wc".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "sort".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "uniq".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "cut".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "tr".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "sed".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "awk".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "curl".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "wget".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "dig".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "nslookup".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "ping".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "traceroute".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "netstat".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "ss".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "id".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "hostname".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "uptime".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "dmesg".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "journalctl".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "systemctl status".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "docker ps".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "docker images".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "docker logs".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "docker inspect".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "kubectl get".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "kubectl describe".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "kubectl logs".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "cargo check".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "cargo test".into(), risk: RiskLevel::Low },
        CommandRule { pattern: "cargo build".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "cargo run".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "make".into(), risk: RiskLevel::Medium },

        // Write/modify commands → Medium
        CommandRule { pattern: "git add".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "git commit".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "git push".into(), risk: RiskLevel::High },
        CommandRule { pattern: "git reset".into(), risk: RiskLevel::High },
        CommandRule { pattern: "git checkout".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "git switch".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "git merge".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "git rebase".into(), risk: RiskLevel::High },
        CommandRule { pattern: "git clean".into(), risk: RiskLevel::High },
        CommandRule { pattern: "cp".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "mv".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "mkdir".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "touch".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "chmod".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "chown".into(), risk: RiskLevel::High },
        CommandRule { pattern: "pip install".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "npm install".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "apt".into(), risk: RiskLevel::High },
        CommandRule { pattern: "yum".into(), risk: RiskLevel::High },
        CommandRule { pattern: "dnf".into(), risk: RiskLevel::High },
        CommandRule { pattern: "brew install".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "docker run".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "docker build".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "kubectl apply".into(), risk: RiskLevel::Medium },
        CommandRule { pattern: "kubectl delete".into(), risk: RiskLevel::High },

        // Destructive commands → High
        CommandRule { pattern: "rm".into(), risk: RiskLevel::High },
        CommandRule { pattern: "rmdir".into(), risk: RiskLevel::High },
        CommandRule { pattern: "dd".into(), risk: RiskLevel::High },
        CommandRule { pattern: "mkfs".into(), risk: RiskLevel::High },
        CommandRule { pattern: "fdisk".into(), risk: RiskLevel::High },
        CommandRule { pattern: "parted".into(), risk: RiskLevel::High },
        CommandRule { pattern: "format".into(), risk: RiskLevel::High },
        CommandRule { pattern: "del ".into(), risk: RiskLevel::High },
        CommandRule { pattern: "erase".into(), risk: RiskLevel::High },
        CommandRule { pattern: "kill".into(), risk: RiskLevel::High },
        CommandRule { pattern: "killall".into(), risk: RiskLevel::High },
        CommandRule { pattern: "pkill".into(), risk: RiskLevel::High },
        CommandRule { pattern: "reboot".into(), risk: RiskLevel::High },
        CommandRule { pattern: "shutdown".into(), risk: RiskLevel::High },
        CommandRule { pattern: "halt".into(), risk: RiskLevel::High },
        CommandRule { pattern: "poweroff".into(), risk: RiskLevel::High },
        CommandRule { pattern: "docker rm".into(), risk: RiskLevel::High },
        CommandRule { pattern: "docker rmi".into(), risk: RiskLevel::High },
        CommandRule { pattern: "docker system prune".into(), risk: RiskLevel::High },
        CommandRule { pattern: "sudo".into(), risk: RiskLevel::High },
        CommandRule { pattern: "su ".into(), risk: RiskLevel::High },
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_policy_classify_tool() {
        let policy = ApprovalPolicy::default();
        assert!(matches!(policy.classify_tool("file_read"), RiskLevel::Low));
        assert!(matches!(policy.classify_tool("file_write"), RiskLevel::Medium));
        assert!(matches!(policy.classify_tool("shell_exec"), RiskLevel::High));
        // Unknown tool defaults to Medium
        assert!(matches!(policy.classify_tool("unknown_tool"), RiskLevel::Medium));
    }

    #[test]
    fn test_default_policy_classify_command() {
        let policy = ApprovalPolicy::default();
        assert!(matches!(policy.classify_command("ls -la"), RiskLevel::Low));
        assert!(matches!(policy.classify_command("rm -rf /"), RiskLevel::High));
        assert!(matches!(policy.classify_command("git add ."), RiskLevel::Medium));
        // Unknown command defaults to Medium
        assert!(matches!(policy.classify_command("some_unknown_cmd"), RiskLevel::Medium));
    }

    #[test]
    fn test_should_block() {
        let policy = ApprovalPolicy::default();
        assert!(!policy.should_block(&RiskLevel::Low));
        assert!(policy.should_block(&RiskLevel::Medium));
        assert!(policy.should_block(&RiskLevel::High));
    }

    #[test]
    fn test_yolo_mode() {
        let mut policy = ApprovalPolicy::default();
        policy.yolo = true;
        assert!(matches!(policy.classify_tool("shell_exec"), RiskLevel::Low));
        assert!(matches!(policy.classify_command("rm -rf /"), RiskLevel::Low));
        assert!(!policy.should_block(&RiskLevel::High));
    }

    #[test]
    fn test_allowlist() {
        let mut policy = ApprovalPolicy::default();
        policy.allowlist = vec!["shell_exec".into()];
        assert!(matches!(policy.classify_tool("shell_exec"), RiskLevel::Low));
        // Non-allowlisted tool still gets default risk
        assert!(matches!(policy.classify_tool("file_write"), RiskLevel::Medium));
    }

    #[test]
    fn test_from_config() {
        let mut extra = HashMap::new();
        extra.insert("custom_tool".into(), RiskLevel::High);
        let policy = ApprovalPolicy::from_config(true, vec!["allowed_tool".into()], extra);
        assert!(policy.yolo);
        assert!(policy.allowlist.contains(&"allowed_tool".to_string()));
        assert!(matches!(policy.tool_rules.get("custom_tool"), Some(RiskLevel::High)));
    }
}
