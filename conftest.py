"""Test-suite path shim.

Kept empty: its presence at the repo root makes pytest add the root to
sys.path, so ``from src.solution import ...`` resolves when running pytest.
Under the standard-library runner, ``python3 -m unittest discover`` already
runs from the repo root with the root on sys.path.
"""
