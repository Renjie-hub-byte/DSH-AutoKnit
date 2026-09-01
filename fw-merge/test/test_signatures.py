"""Tests for fw_merge.signatures (AST signature extraction)."""

from fw_merge.signatures import Signature, extract_signatures


def test_extract_function_and_class_methods(tmp_path):
    from helpers import _write

    root = str(tmp_path / "m")
    _write(f"{root}/src/svc.py", "def parse(value):\n    return value\n\nclass Order:\n    def total(self, tax=0.1):\n        return tax\n")
    sigs = extract_signatures(f"{root}/src")
    assert "parse" in sigs
    assert sigs["parse"].params == ["value"]
    assert sigs["parse"].n_defaults == 0
    assert "Order" in sigs
    assert "Order.total" in sigs
    assert sigs["Order.total"].params == ["self", "tax"]
    assert sigs["Order.total"].n_defaults == 1


def test_signature_key_detects_vararg_and_kwarg():
    a = Signature("f", "a.py", 1, ["x"])
    b = Signature("f", "b.py", 1, ["x"], has_kwarg=True)
    assert a.key() != b.key()
    c = Signature("f", "c.py", 1, ["x", "y"])
    assert a.key() != c.key()
    assert a.key() == Signature("f", "d.py", 9, ["x"]).key()


def test_extract_skips_unparsable_file(tmp_path):
    from helpers import _write

    root = str(tmp_path / "m")
    _write(f"{root}/src/good.py", "def ok():\n    return 1\n")
    _write(f"{root}/src/broken.py", "def broken(:\n    this is not python\n")
    sigs = extract_signatures(f"{root}/src")
    assert "ok" in sigs
    assert "broken" not in sigs
