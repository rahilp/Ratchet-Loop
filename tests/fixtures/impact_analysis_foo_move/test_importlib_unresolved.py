"""CP2-X07 fixture: loads module_a dynamically via importlib (data flow this static pass
deliberately never tracks -- see the module docstring's documented precision bar) and then patches
the moving symbol by name on the resulting object. The receiver cannot be proven to be module_a OR
proven not to be, so this must surface as manual-check-needed rather than being silently dropped."""
import importlib


def test_patches_dynamically_loaded_module():
    mod = importlib.import_module("pkg.module_a")
    mod.foo = lambda: "patched"  # MARKER: unresolved-receiver-monkeypatch


def test_reads_dynamically_loaded_module():
    mod = importlib.import_module("pkg.module_a")
    real_foo = mod.foo  # MARKER: unresolved-receiver-read
    return real_foo
