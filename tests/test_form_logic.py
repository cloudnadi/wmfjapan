"""Python and JavaScript condition evaluators agree on the shared fixture cases."""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas"))

import form_engine  # noqa: E402

CASES = json.loads((ROOT / "tests" / "fixtures" / "form_conditions.json").read_text())


class PythonConditionTests(unittest.TestCase):
    def test_fixture_cases(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(form_engine.evaluate_condition(case["cond"], case["answers"]), case["expected"])


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class JavaScriptConditionTests(unittest.TestCase):
    def test_fixture_cases(self):
        script = (
            "import {evaluateCondition} from %s;"
            "import fs from 'fs';"
            "const cases = JSON.parse(fs.readFileSync(%s, 'utf8'));"
            "console.log(JSON.stringify(cases.map(c => evaluateCondition(c.cond, c.answers))));"
        ) % (json.dumps((ROOT / "frontend/src/formLogic.js").as_uri()),
             json.dumps(str(ROOT / "tests/fixtures/form_conditions.json")))
        out = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        results = json.loads(out.stdout)
        for case, got in zip(CASES, results):
            with self.subTest(case["name"]):
                self.assertEqual(got, case["expected"])


if __name__ == "__main__":
    unittest.main()
