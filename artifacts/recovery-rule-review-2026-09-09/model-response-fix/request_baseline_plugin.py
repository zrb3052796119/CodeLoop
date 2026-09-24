"""Load the captured pre-control module only after pytest HOME isolation."""
import importlib.util
from pathlib import Path
import sys


def pytest_sessionstart(session):
    name = 'minicode.reflection_llm'
    path = Path(__file__).with_name('source_before') / 'reflection_llm.py'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
