# Automated development shifts

The project runner adapts UniqueOS's isolated worktree, bounded Codex batches, stop marker, phase log and durable handoff workflow. scripts/ai/shift_phase.sh is imported from UniqueOS at commit 63caccba8c36a5bce7351ffc28b763f7563c5809. Its original owner notice is retained in UNIQUEOS-LICENSE.txt. The project-specific supervisor is scripts/ai/shift.py.

Run python scripts/ai/shift.py --setup-only to verify prerequisites. Run python scripts/ai/shift.py --batches 3 --minutes 60 to begin. Each invocation creates a branch and sibling worktree and runs at most three Codex batches within the overall deadline. Authentication uses the installed Codex CLI session. Workspace-write sandboxing is explicit. The worker selects the next roadmap task, runs checks, commits local changes and writes a handoff. Review its branch and publish through the ordinary Git workflow.

Create .state/shifts/STOP in the launching checkout to terminate the current batch and end the shift. Logs and worktree paths are printed at startup. A nonzero model exit stops the shift. An exclusive lock prevents overlapping supervisors in the same checkout. After an abnormal supervisor termination, inspect remaining processes before removing a stale supervisor.lock. The worktree persists for inspection and recovery.

The default model follows local Codex configuration. No model call runs during setup validation. The imported Bash phase helper is available for manual instrumentation through Git Bash, while the Python supervisor writes its own portable timeline.

Codex CLI reference https://developers.openai.com/codex/noninteractive/
