"""Release image context regression: exercise only files admitted/copied by Docker."""
import fnmatch
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


def admitted(name):
    included = True
    for line in (ROOT / '.dockerignore').read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        negate = line.startswith('!')
        pattern = line[1:] if negate else line
        if fnmatch.fnmatchcase(name, pattern.rstrip('/')) or name.startswith(pattern.rstrip('/') + '/'):
            included = negate
    return included


class ReleasePackagingTests(unittest.TestCase):
    def test_image_copy_context_imports_all_runtime_modules(self):
        with tempfile.TemporaryDirectory(prefix='fomobill-image-stage-') as directory:
            stage = Path(directory)
            for line in (ROOT / 'Dockerfile').read_text().splitlines():
                words = shlex.split(line)
                if not words or words[0].upper() != 'COPY':
                    continue
                self.assertEqual(words[-1].rstrip('/'), '/app' if words[1] != 'static' else '/app/static')
                for source in words[1:-1]:
                    src = ROOT / source
                    self.assertTrue(admitted(source), f'{source} excluded from build context')
                    if src.is_dir():
                        for child in src.rglob('*'):
                            rel = child.relative_to(ROOT)
                            if child.is_file() and admitted(str(rel)):
                                dest = stage / rel
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.copyfile(child, dest)
                    else:
                        shutil.copyfile(src, stage / source)
            # -I ignores cwd/PYTHONPATH: explicitly admit ONLY the staged directory.
            script = "import sys; sys.path.insert(0, sys.argv[1]); import app, holders, holder_history_sources; from holders import Holders; assert callable(Holders().wallet_history); print('STAGED_RUNTIME_IMPORT_OK')"
            result = subprocess.run([sys.executable, '-I', '-c', script, str(stage)],
                                    cwd=stage, text=True, capture_output=True,
                                    env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('STAGED_RUNTIME_IMPORT_OK', result.stdout)
            for name in ('index.html', 'app.js', 'style.css'):
                self.assertTrue((stage / 'static' / name).is_file())

    def test_context_excludes_secrets_state_and_test_artifacts(self):
        for name in ('.env', 'data/state.db', 'test_release_packaging.py',
                     'tests/fixtures/live-tx-1.json', '__pycache__/app.pyc', 'docs/holders-contract.md'):
            self.assertFalse(admitted(name), name)


if __name__ == '__main__':
    unittest.main()
