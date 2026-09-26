import importlib.util
import json
import pathlib

_SPEC_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"
_spec = importlib.util.spec_from_file_location("scan_secrets", _SPEC_PATH)
scan_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_secrets)


def test_preview_does_not_contain_the_raw_secret():
    token = "ghp_" + "A" * 36
    content = f"    GITHUB_TOKEN = '{token}'  # inline comment"

    findings = scan_secrets._match_content(content, "conftest.py", {"ref": "HEAD"})

    assert findings, "expected the synthetic token to be detected"
    dumped = json.dumps(findings)
    assert token not in dumped
    for f in findings:
        assert token not in f.get("preview", "")
        assert token not in f.get("match", "")


def test_example_word_does_not_hide_a_real_looking_token_elsewhere_in_the_line():
    token = "ghp_" + "B" * 36
    padding = "abcdefghij" * 3
    content = f"example {padding} {token}"

    findings = scan_secrets._match_content(content, "notes.md", {"ref": "HEAD"})

    assert any(f["type"] == "github-pat" for f in findings), (
        "a real-looking token should still be reported even though the word "
        "'example' appears earlier in the same line, unrelated to the match"
    )


def test_placeholder_next_to_the_match_still_suppresses_it():
    content = "TOKEN=your_placeholder_value_here"

    findings = scan_secrets._match_content(content, ".env.dist", {"ref": "HEAD"})

    assert findings == []
