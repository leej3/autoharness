# Native agent hosts, including Codex

The `autoharness.native` bridge reuses AutoHarness's bundle builder, skill index, intent schema, queue, validators, promoter, and ledger.
The calling host owns agent execution.
It does not launch Claude or another model process.
Existing Claude hooks and defaults are unchanged.

Use Python 3.11+ and set `PYTHONPATH` to this checkout's `src` directory.
No model API key is needed by the bridge itself.
Your host provides the model connection.
Run all commands with the same unique run ID and explicit roots.
Each root holds `skills/` and `autoharness/`; choose separate temporary roots for initial trials.
For project-owned Codex skills the project root can be the project's `.agents` directory.
Never point a trial at APM-managed deployed skills.
This adapter does not install itself or change host configuration.

```sh
export PYTHONPATH="/absolute/path/to/autoharness/src"
python3 -m autoharness.native prepare \
  --project-root /tmp/ah-project --global-root /tmp/ah-global \
  --run-id trial-001 --input /absolute/path/to/evidence.txt
```

The JSON output contains a redacted evidence bundle, an instruction, and the existing intent schema.
In Codex, give those to a native subagent and ask it to return zero or more proposal objects.
Do not give that subagent permission to write skill trees.
This is an orchestration instruction, not a sandbox enforced by this CLI.
The parent saves each proposal to a JSON file and stages it:

```sh
python3 -m autoharness.native stage \
  --project-root /tmp/ah-project --global-root /tmp/ah-global \
  --run-id trial-001 --input /absolute/path/to/proposal.json
python3 -m autoharness.native inspect \
  --project-root /tmp/ah-project --global-root /tmp/ah-global \
  --run-id trial-001
python3 -m autoharness.native apply \
  --project-root /tmp/ah-project --global-root /tmp/ah-global \
  --run-id trial-001
```

`stage` checks proposals without changing skills.
`apply` runs the existing deterministic promoter, reports each verdict, and consumes the queue.
A rejection returns a nonzero exit status; a mixed batch may already have applied successful proposals.
Inspect verdicts before deciding whether to retry.
Application is not transactional across proposals and does not establish that a skill is effective.

For consolidation use `prepare --role curator` with the same roots and run ID; it lists only agent-authored skills.
The parent can delegate reflection, consolidation, and independent evaluation as distinct native agent tasks.
Retain evaluation evidence separately from structural validation and use counts.

## Compatibility boundary

This is an explicitly invoked bridge, not an automatic completion hook or a complete Codex plugin.
It deliberately does not interpret Codex transcripts as Claude transcripts.
It has offline integration coverage for proposal preparation, staging, inspection, and application; live Codex and Claude host smoke tests remain necessary before claiming equivalent automatic behavior.

Next integration work is host event normalization, task identity and idempotent delivery, native dispatch, and plugin packaging.
Command hooks have an official [Codex migration path](https://developers.openai.com/plugins/guides/submit-claude-plugin).
Host-specific launchers and manifests should surround the same shared core.
