mod trait_def;
mod ga_adapter;
mod subprocess_adapter;
mod registry;

pub use trait_def::{Runner, RunnerContext, RunnerOutput, RunnerStatus};
pub use ga_adapter::GaRunner;
pub use subprocess_adapter::SubprocessRunner;
pub use registry::RunnerRegistry;
