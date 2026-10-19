"""English UI regression: first-party copy, including decoded backend literals."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent
CYRILLIC = re.compile(r'[\u0400-\u04ff]')


class EnglishUITests(unittest.TestCase):
    def test_static_copy_and_document_language(self):
        for path in (ROOT / 'static').iterdir():
            if path.suffix in ('.html', '.js', '.css', '.json'):
                self.assertIsNone(CYRILLIC.search(path.read_text()), path.name)
        html = (ROOT / 'static/index.html').read_text()
        self.assertIn('<html lang="en">', html)
        for text in ('Find holders', 'Monitoring', 'Holder trades',
                     'I consent to server-side monitoring', 'Historical case study'):
            self.assertIn(text, html)

    def test_backend_decoded_strings_are_english(self):
        # AST decoding also catches escaped Unicode, not just literal characters.
        for name in ('app.py', 'monitoring.py', 'notifications.py',
                     'holders.py', 'holder_history_sources.py'):
            for node in ast.walk(ast.parse((ROOT / name).read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(CYRILLIC.search(node.value), (name, node.lineno))


if __name__ == '__main__':
    unittest.main()
