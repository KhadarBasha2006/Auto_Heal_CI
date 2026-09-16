import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402


@pytest.fixture
def broken_repo(tmp_path: Path) -> Path:
    """A repository containing one defect of each kind the agent detects."""
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)

    (root / "src" / "pkg" / "__init__.py").write_text("")
    (root / "src" / "pkg" / "calc.py").write_text(
        "import os\n"
        "import sys, json\n"
        "from .helpers import missing_helper\n"
        "\n"
        "def add(a, b):\n"
        "\ttotal = a + b\n"
        "\treturn total\n"
        "\n"
        "def scaled(values):\n"
        "    out = []\n"
        "    for value in values:\n"
        "      out.append(value * 2)   \n"
        "    if out == None:\n"
        "        return []\n"
        "    return out\n"
        "\n"
        "def uses_undefined(x):\n"
        "    return not_defined_anywhere(x)\n"
    )
    (root / "src" / "pkg" / "broken_syntax.py").write_text(
        "def f(a):\n    result = a +\n    return result\n"
    )
    (root / "package.json").write_text('{ "name": "demo", "version": "1.0.0", }\n')
    (root / "requirements.txt").write_text("requests\n")
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non:\n  push:\n    branches: [main]\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    steps:\n      - uses: actions/checkout@v4\n      - run: pytest\n"
    )
    return root


@pytest.fixture
def clean_repo(tmp_path: Path) -> Path:
    root = tmp_path / "clean"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "math_utils.py").write_text(
        '"""Helpers."""\n\n\ndef add(a, b):\n    return a + b\n'
    )
    (root / "main.py").write_text(
        '"""Entry point."""\n\nfrom pkg.math_utils import add\n\n\n'
        "def main():\n    print(add(1, 2))\n"
    )
    return root
