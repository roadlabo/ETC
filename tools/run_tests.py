"""Run test modules with independent Qt applications and temporary settings."""
import os
from pathlib import Path
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS', '--disable-gpu')

# Keep WebEngine profiles and Qt application lifetimes isolated between modules.
failed = []
for module in sorted((ROOT / 'tests').glob('test_*.py')):
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env['ETC_PROJECT_STATE'] = str(Path(tmp) / 'project.json')
        try:
            result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover',
                                     '-s', str(ROOT / 'tests'), '-p', module.name, '-v'], env=env, timeout=90)
            if result.returncode:
                failed.append(module.stem)
        except subprocess.TimeoutExpired:
            failed.append(module.stem + ' (timeout)')
print('Failed modules: ' + ', '.join(failed) if failed else 'All test modules passed.', flush=True)
sys.exit(bool(failed))
