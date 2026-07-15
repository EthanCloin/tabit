---
name: night-shift-manager
description: Mechanics for running a delegated, budget-capped roadmap of tabit improvements efficiently — how to delegate to subagents, honor the caps, and compact cleanly. Load when the user has asked me to drive a batch of tabit work; it describes HOW to execute, not authorization to start. Scope and continuation always come from the user's current instructions, not this file.
---

# night-shift-manager

This skill is a set of **execution mechanics**, not a grant of authority. Whether to run, how
many rounds to do, and when to keep going all come from the **user's current instructions** and
the active plan file — not from this document. If those are absent or ambiguous, ask the user
before proceeding. A future session should not treat this file as permission to act
autonomously; it only describes how to do the work well once the user has asked for it.

## Budget awareness
- If the user set a usage budget (e.g. "≤ 25% of the weekly allotment"), treat it as a hard
  stop: when it's near, finish any in-flight PR, leave no half-created issues, post a short
  status, and stop.
- Biggest cost lever is **opus/high-effort subagents** — reserve them for genuinely hard
  tickets; use sonnet for medium work; keep prompts tight. Development > ceremony.

## Delegation (keep my own context lean)
- Push each ticket's implementation **and its decision churn** into a subagent whose context
  clears between tasks. I stay orchestration-level: create issue → dispatch → record PR URL → next.
- Give subagents the `voicevault-contributor` skill so my dispatch prompts stay short.
- Match model/effort to complexity: easy→sonnet/low, medium→sonnet/medium, hard→opus/high.
- Don't read large subagent transcripts back into my context; trust their summary + the PR.

## Caps to honor (when the user sets them)
- **≤ 5 issues assigned to subagents at once.**
- **Don't create new issues while 5+ issues sit unassigned.** Assign each issue to a subagent
  right after creating it so the unassigned backlog stays ~0.

## Per-round mechanics
When the user has asked for a roadmap/round of work:
1. Work the current roadmap from the plan file / open issues.
2. Create issues (respect the backlog cap), dispatch subagents (respect the concurrency cap),
   branch + PR per ticket, stacked on the integration branch to respect dependencies.
3. Record outcomes (PR URLs) in the plan file and `CLAUDE.md`.

Deciding *whether* to start another round, and what it contains, is a user-facing decision:
follow the user's standing instruction if they gave one, otherwise check in.

## Compaction discipline
- When context grows large, **compact** rather than stall.
- **Before compacting, run a friction review**: scan the session for wasted effort, repeated
  mistakes, or coordination friction with subagents. If found, update this skill and/or
  `voicevault-contributor` and commit the change.
- Keep durable state outside my head: the plan file, GitHub issues/PRs, and `CLAUDE.md`, so I
  can resume from those after compaction.

## Living docs
- Keep repo-root **`CLAUDE.md`** current as I learn workflow issues (replaces deleted WORKING.md).
- All skills are project-scoped and version-controlled under `.claude/skills/`; commit changes.
