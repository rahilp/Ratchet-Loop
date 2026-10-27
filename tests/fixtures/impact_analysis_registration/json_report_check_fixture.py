"""CP3 fixture (round-2 fix for X2, not a planned case): an ordinary behavior test that reads a
generated JSON report and checks a case id is present in it with a plain containment check. Used
to prove this is NOT classified as a project-source scan (its read target's literal extension is
`.json`, an obviously-data extension, not `.py`) -- no inventory item, no manual-check-needed."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "report.json"


def test_expected_case_id_is_present():
    results = REPORT.read_text()
    assert "CP1-X01" in results
