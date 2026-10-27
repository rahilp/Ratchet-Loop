"""CP7 fixture (M-shaped): the new test file a builder would add while extracting `_load_state` out
of pkg.module_a into a new sibling module. This is a STATIC SCAN FIXTURE, never actually executed by
pytest/CI in this run -- the repoint/registration passes only read and AST-parse its text. It carries
BOTH re-point hazard shapes the CP7 corpus must prove the scanner catches end-to-end (per the
director's note and .ratchet/learnings.md's 2026-10-26 incomplete-source-scan-detection entries,
citing the real incidents B1, CP5-X78 and lane M's own CP6 regression):

(a) a RUNTIME MONKEYPATCH site (`patch.object(module_a, "_load_state")`), the dominant
    brownfield-extraction hazard -- a fault-injection test shaped exactly like lane M's CP6 regression
    (cp4y_deny_hook_env patching m._write_record, which silently stopped intercepting once
    record_hook_env moved to a different module).
(b) a disguised SOURCE-INSPECTION site: a check whose own function name and comments never mention
    module_a, pkg.module_b or any move at all, that just happens to AST-scan pkg/module_a.py's own
    source text looking for the literal helper name `_load_state` -- the exact CP5-X78 shape.
"""
import ast
from pathlib import Path
from unittest.mock import patch

import pkg.module_a as module_a

HERE = Path(__file__).resolve().parent
_MODULE_A_SOURCE = HERE / "pkg" / "module_a.py"


def test_fault_injection_when_disk_is_unreadable():
    """A fault-injection test that patches the helper by name on its current home module -- this
    silently stops intercepting once the helper moves to the sibling module, unless this site is
    re-pointed in the same step."""
    with patch.object(module_a, "_load_state") as fake:  # MARKER: monkeypatch-by-name
        fake.side_effect = OSError("disk full")
        assert module_a is not None  # placeholder body; the patch above is the hazard being proved


def test_no_forbidden_helpers_defined_in_core():
    """Named and commented with no reference to module_a, pkg.module_b or any planned move -- it
    just happens to AST-scan the core module's own source text for a literal helper name, the exact
    disguised shape a plain grep for the move's module names would miss."""
    tree = ast.parse(_MODULE_A_SOURCE.read_text())
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    assert "_load_state" in defined  # MARKER: source-inspection-scan


# ---------------------------------------------------------------------------------------------
# CP7 round-1 adversarial fix (codex-standards/X1): the corpus above exercised only ONE runtime
# monkeypatch form (patch.object) and one source-inspection scan. The director's 2026-10-26
# learnings entry requires CP7's corpus to prove a COMPREHENSIVE set of re-point patterns. The
# functions and import below add the remaining required runtime monkeypatch forms (bare setattr,
# direct attribute assignment, pytest's monkeypatch.setattr fixture) plus a direct importer of the
# moved symbol. Appended after the two original marked tests; none of the lines above this comment
# were changed.
# ---------------------------------------------------------------------------------------------


def test_fault_injection_via_bare_setattr():
    """A bare `setattr(module_a, "_load_state", fake)` runtime monkeypatch form -- the same hazard
    shape as test_fault_injection_when_disk_is_unreadable above, spelled with the builtin
    `setattr()` instead of `patch.object`."""
    def fake(*_args, **_kwargs):
        raise OSError("disk full")
    setattr(module_a, "_load_state", fake)  # MARKER: setattr-call


def test_fault_injection_via_direct_attribute_assignment():
    """A direct `module_a._load_state = fake` attribute-assignment monkeypatch form -- no call at
    all, just a plain assignment onto the module object's attribute."""
    def fake(*_args, **_kwargs):
        raise OSError("disk full")
    module_a._load_state = fake  # MARKER: attribute-assignment


def test_fault_injection_via_monkeypatch_fixture(monkeypatch):
    """A pytest `monkeypatch.setattr(module_a, "_load_state", fake)` fixture-style form -- CP2's
    repoint subcommand was specifically hardened in round 3 of its own adversarial review to
    recognize this spelling."""
    def fake(*_args, **_kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(module_a, "_load_state", fake)  # MARKER: monkeypatch-setattr


from pkg.module_a import _load_state  # MARKER: direct-importer
