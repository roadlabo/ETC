"""Native launcher layout and real batch dispatch smoke tests."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('etc_launcher', ROOT / 'src/00_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontDatabase

APP = QApplication.instance() or QApplication(['test_launcher'])
for font_path in Path('C:/Windows/Fonts').glob('YuGoth*.ttc'):
    QFontDatabase.addApplicationFont(str(font_path))
APP.setFont(QFont('Yu Gothic UI', 9))


class LauncherTests(unittest.TestCase):
    def test_immediate_feedback_and_repeat_click_suppression(self):
        from unittest.mock import Mock, patch
        from PyQt6.QtTest import QTest
        window = launcher.Launcher()
        card = window.cards[0]
        process = Mock()
        process.poll.return_value = 0
        with patch.object(launcher, 'launch_tool', return_value=(process, ROOT / 'logs/test.log')) as start:
            window.launch(card)
            self.assertTrue(card.running)
            start.assert_not_called()
            window.launch(card)
            QTest.qWait(100)
            start.assert_called_once()
            window.poll()
            self.assertTrue(card.running)
            window.launch(card)
            start.assert_called_once()
            card.started_at -= 9
            window.poll()
            self.assertFalse(card.running)
        window.close()

    def test_catalog_covers_all_tools(self):
        batches = {p.name for p in (ROOT / 'bat').glob('*.bat') if not p.name.startswith('00_')}
        self.assertEqual(batches, {t.batch for t in launcher.TOOLS})
        self.assertEqual(len(launcher.TOOLS), len({t.number for t in launcher.TOOLS}))

    def test_all_cards_and_descriptions_fit(self):
        window = launcher.Launcher()
        window.show()
        for width, height in [(1920, 1000), (1366, 720), (1280, 680)]:
            window.resize(width, height)
            APP.processEvents()
            self.assertEqual((window.width(), window.height()), (width, height))
            picture = window.grab()
            for card in window.cards:
                self.assertTrue(card.text_fits, f'{width}x{height}: {card.tool.number}')
                self.assertTrue(window.centralWidget().rect().contains(card.geometry()))
                self.assertGreaterEqual(card.text_pixel_size, 10)
            output = ROOT / 'logs' / f'launcher_preview_{width}.png'
            output.parent.mkdir(exist_ok=True)
            picture.save(str(output))
        window.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows batch launch')
    def test_launch_unicode_spaces_and_shell_characters(self):
        with tempfile.TemporaryDirectory(prefix='ETC 日本語 & %検証% ') as tmp:
            root = Path(tmp)
            (root / 'bat').mkdir()
            tool = launcher.TOOLS[0]
            (root / 'bat' / tool.batch).write_bytes(b'@echo off\r\necho launched>marker.txt\r\nexit /b 0\r\n')
            process, log = launcher.launch_tool(tool, root)
            self.assertEqual(process.wait(timeout=15), 0, log.read_bytes())
            self.assertEqual((root / 'marker.txt').read_text().strip(), 'launched')

    def test_missing_batch_and_unknown_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                launcher.launch_tool(launcher.TOOLS[0], Path(tmp))
        with self.assertRaises(ValueError):
            launcher.launch_tool(None)

    def test_area_builder_direct_entrypoint(self):
        # Do not inject src into this child: reproduce the bundled runtime launch.
        import subprocess
        result = subprocess.run([sys.executable, str(ROOT / 'src/14_area_builder.py'), '--help'],
                                capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
