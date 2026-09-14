import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_release', ROOT / 'tools/build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseLayoutTests(unittest.TestCase):
    def test_distribution_excludes_development_and_preserves_previous_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in builder.CONTENTS:
                if '.' in name:
                    (root / name).write_text('readme')
                else:
                    (root / name).mkdir()
            for name in ['runtime/python/pythonw.exe', 'docs/user.md', 'docs/development/design.md',
                         'src/app.py', 'src/__pycache__/app.pyc', 'tiles/tile.png', 'tests/test.py',
                         'tools/dev.py', 'samples/sample.csv', 'userdata/project.json']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            destination = root / 'release/ETC'
            with patch.object(builder, 'ROOT', root):
                builder.build(destination)
                for name in ['docs/development', 'tests', 'tools', 'samples', 'userdata', 'src/__pycache__']:
                    self.assertFalse((destination / name).exists(), name)
                for name in ['docs/user.md', 'src/app.py', 'tiles/tile.png', 'runtime/python/pythonw.exe', '00_ETC_launcher.bat']:
                    self.assertTrue((destination / name).is_file(), name)
                with self.assertRaises(FileExistsError):
                    builder.build(destination)
