import json

from autoharness import native


def invoke(tmp_path, capsys, command, *extra):
    status = native.main([command, "--project-root", str(tmp_path / "project"),
                          "--global-root", str(tmp_path / "global"),
                          "--run-id", "native-test", *extra])
    return status, json.loads(capsys.readouterr().out)


def test_native_round_trip(tmp_path, capsys):
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("Repeated ISO date formatting task.")
    status, prepared = invoke(tmp_path, capsys, "prepare", "--input", str(evidence))
    assert status == 0
    assert "Repeated ISO" in prepared["bundle"]
    assert "action" in prepared["intent_schema"]["required"]
    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({
        "action": "create", "name": "iso-date", "reason": "repeated date formatting",
        "evidence": "Repeated ISO date formatting task.",
        "body": "---\nname: iso-date\ndescription: Use when formatting a date as ISO.\n---\n"
                "# ISO date\nUse strftime.\n",
    }))
    status, staged = invoke(tmp_path, capsys, "stage", "--input", str(proposal))
    assert status == 0 and staged["ok"]
    assert not (tmp_path / "project/skills/iso-date/SKILL.md").exists()
    _, inspected = invoke(tmp_path, capsys, "inspect")
    assert len(inspected["intents"]) == 1
    status, applied = invoke(tmp_path, capsys, "apply")
    assert status == 0, applied
    assert (tmp_path / "project/skills/iso-date/SKILL.md").exists()
    _, inspected = invoke(tmp_path, capsys, "inspect")
    assert inspected["intents"] == []


def test_bad_intent_does_not_queue(tmp_path, capsys):
    proposal = tmp_path / "bad.json"
    proposal.write_text('{"action": "create"}')
    status, out = invoke(tmp_path, capsys, "stage", "--input", str(proposal))
    assert status == 1 and not out["ok"]
    _, inspected = invoke(tmp_path, capsys, "inspect")
    assert inspected["intents"] == []


def test_bad_run_id_cannot_escape_root(tmp_path, capsys):
    status = native.main(["apply", "--project-root", str(tmp_path / "p"),
                          "--global-root", str(tmp_path / "g"), "--run-id", "../../escape"])
    assert status == 1
    assert "unsafe run id" in json.loads(capsys.readouterr().out)["error"]
