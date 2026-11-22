import ast
import unittest
from pathlib import Path


class StrategyModuleSyntaxTests(unittest.TestCase):
    def test_strategy_module_parses_and_imports_math(self):
        source = Path("strategy.py").read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("import math", source)


if __name__ == "__main__":
    unittest.main()
