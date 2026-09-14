"""Initialize QtWebEngine before any test creates QApplication."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt6.QtCore import Qt, QCoreApplication
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PyQt6 import QtWebEngineWidgets

with tempfile.TemporaryDirectory() as tmp:
    os.environ['ETC_PROJECT_STATE'] = str(Path(tmp) / 'project.json')
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'unreleased/tests'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
