import json
from pathlib import Path
from urllib.parse import urlparse


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "evals" / "inspect_urls.json"


def test_inspect_eval_manifest_has_unique_live_cases_and_valid_expectations():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    cases = manifest["cases"]

    assert manifest["schema"] == "paper.inspect-eval.v1"
    assert len(cases) >= 20
    assert len({case["id"] for case in cases}) == len(cases)
    assert len({case["url"] for case in cases}) == len(cases)

    for case in cases:
        expected = case["expected"]
        parsed = urlparse(case["url"])

        assert parsed.scheme == "https"
        assert parsed.hostname
        assert case["shape"]
        assert case["rationale"]
        assert expected["verdict"] in {"accept", "reject"}
        if expected["stage"] == "readability":
            assert case["source_type"] in {"html", "pdf"}
            assert expected == {"stage": "readability", "verdict": expected["verdict"]}
        else:
            assert expected == {"stage": "fetch", "verdict": "reject", "http_status": 415}
