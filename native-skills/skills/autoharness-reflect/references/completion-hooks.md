# Optional Codex completion hooks

Install at user scope from the deployed skill directory:

```sh
python3 scripts/completion.py install
```

This merges four command hooks into `~/.codex/hooks.json`, preserving other hooks and saving a backup before modification.
It uses the current Python executable; no network call or model process runs inside a hook.
Review and trust the new commands in Codex's `/hooks` interface before expecting execution.
Installing a skill alone does not install or trust hooks.
Existing tasks may need restarting.

The script supports macOS and Linux (POSIX file locking).
To disable collection, disable these four commands in `/hooks`; other hook handlers are independent.
Do not replace all hooks with an old backup if other handlers changed meanwhile.

## Behavior

- UserPromptSubmit starts a turn clock, without retaining prompt text.
- PostToolUse counts distinct tool IDs and looks for skill paths in arguments.
  A path is a candidate only: writes, failed reads, and installation commands can mention skills too.
  Reads in earlier turns, indirect paths, and some tool paths will not be detected.
  No tool arguments or responses are retained.
- Stop appends one turn observation.
  With candidates, it requests at most one native continuation for the agent to confirm material use and record a concise report.
  It skips plan-mode continuations, prior Stop continuations, and routine recorder/reflection bookkeeping.
  The agent honors user opt-outs and stop requests.
- Interrupt records the interrupted turn without requesting reflection.

Stop means a turn ended, not that the user's task succeeded.
Host observations therefore have unknown outcome.
Turn wall time includes tool waits and is not per-skill execution time.
Missing start events produce null duration.

A normal successful use requires only a report, with no qualitative narrative.
An exceptional reusable lesson can trigger `autoharness-reflect` in the native agent; collection itself does not authorize changing a skill or publishing.
The hook does not spawn a separate CLI agent or invoke an API model.

## Schemas and storage

[Report schema](../schemas/report-v1.schema.json) defines `usages`, with required skill, purpose, outcome, and reflection fields.
Optional fields capture measured skill duration, friction category, correction count, and an existing feedback ID.
The stdlib reporter enforces the same field constraints and additionally rejects duplicate skill names.
[Observation schema](../schemas/observation-v1.schema.json) defines host observations and agent-reported records separately.

Records and retry state live in `~/.local/state/autoharness/completion/` (or under XDG_STATE_HOME).
AUTOHARNESS_COMPLETION_STORE overrides this for tests.
These are runtime telemetry, separate from Workshop's shareable feedback records and private overlay; they are not automatically committed or published.
Reports can link an existing Workshop feedback ID.
Do not sum the two stores as independent uses.

JSONL records have deterministic IDs derived from session and turn IDs; raw IDs, project paths, prompts, transcripts, and outputs are not stored.
Hashes are pseudonymous correlators, not anonymization.
Files may still contain skill names, model names, and concise purpose categories; classify selected exports normally.
Identical report retries are ignored; conflicting reports are rejected.
File locks serialize writes.
A process crash between JSONL append and state save can duplicate an ID: consumers must deduplicate by `(kind, id)`.
No retention cleanup is automatic.

## What to assess during the trial

Compare candidate turns with confirmed uses and rejected candidates.
Note missed implicit uses and whether the extra continuation is useful or intrusive.
Outcome, friction, and correction counts are agent assertions, not independent grading.

This trial does not infer token cost, exact skill activation, per-skill timing, human satisfaction, or causal improvement.
If those become useful, add explicit start/end usage markers and user corrections at the point of use, then compare candidate changes on matched tasks.
Avoid adding more fields without a concrete question they will answer.

Run `python3 scripts/completion.py status` for deduplicated counts of observed turns, candidate turns, reported turns, confirmed uses, empty reports, missing start events, reflection requests, and outcomes.
This is the first coverage check after trusting the hooks.
Zero observed turns means delivery is unverified, not that there were no skill uses.
