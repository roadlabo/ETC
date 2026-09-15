"""Exercise the copied runtime, launcher and actual local tile URL."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile

root = Path(sys.argv[1]).resolve()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
sys.path.insert(0, str(root / 'src'))
from PyQt6.QtCore import QEventLoop, QTimer, QUrl, QCoreApplication, Qt
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWidgets import QApplication
from common import project_settings
from common.launcher_catalog import TOOLS
import offline_leaflet

app = QApplication(['release_smoke'])
app.setQuitOnLastWindowClosed(False)
from PyQt6.QtGui import QFontDatabase, QFont
for font in Path('C:/Windows/Fonts').glob('YuGoth*.ttc'):
    QFontDatabase.addApplicationFont(str(font))
app.setFont(QFont('Yu Gothic UI', 9))
print('Qt runtime initialized', flush=True)
web = QWebEngineView()
print('WebEngine initialized', flush=True)
with tempfile.TemporaryDirectory() as tmp:
    project_settings.STATE_PATH = Path(tmp) / 'project.json'
    spec = importlib.util.spec_from_file_location('release_launcher', root / 'src/00_launcher.py')
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    window = launcher.Launcher()
    print('Launcher constructed', flush=True)
    window.show()
    app.processEvents()
    assert launcher.ROOT == root
    assert all((root / 'bat' / tool.batch).is_file() for tool in TOOLS)
    window.grab().save(str(Path(__file__).resolve().parents[1] / 'logs/release_launcher.png'))
    print('Launcher captured', flush=True)
    web.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    tile = root / 'tiles/gsi_pale/9/446/202.png'
    assert tile.is_file()
    template = offline_leaflet.LOCAL_GSI_TILE_TEMPLATE
    assert template == (root / 'tiles/gsi_pale').as_uri() + '/{z}/{x}/{y}.png'
    web.setHtml('<html><body><img id="tile" src="' + template.format(z=9, x=446, y=202) + '"></body></html>', QUrl.fromLocalFile(str(root / 'src') + '/'))
    print('Local tile requested', flush=True)
    loop = QEventLoop()
    loaded = []
    def receive(value):
        if value:
            loaded.append(value)
            loop.quit()
    timer = QTimer()
    timer.timeout.connect(lambda: web.page().runJavaScript("document.getElementById('tile')?.naturalWidth > 0", receive))
    timer.start(100)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    timer.stop()
    web.close()
    window.close()
    assert loaded, 'Relocated local tile did not load'
print(f'Copied runtime, {len(TOOLS)} launcher targets and local tile loading: OK')
