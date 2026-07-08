mod trait_def;
mod ga_adapter;
mod registry;

pub use trait_def::{Runner, RunnerContext, RunnerOutput, RunnerStatus};
pub use ga_adapter::GaRunner;
pub use registry::RunnerRegistry;
