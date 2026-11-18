"""Install Step 17 files into existing web_app without replacing your website.

Run from SLT_Payment_Project root after extracting the Step 17 ZIP there:
    python 17_install_consent_demo.py
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'web_app' / 'app.py'
BASE = ROOT / 'web_app' / 'templates' / 'base.html'
BP = ROOT / 'web_app' / 'consent_demo.py'
PAGE = ROOT / 'web_app' / 'templates' / 'consent_demo.html'

if not all(p.is_file() for p in (APP, BASE, BP, PAGE)):
    raise SystemExit('Missing website or Step 17 files. Extract into the main project folder and try again.')

app_text = APP.read_text(encoding='utf-8')
base_text = BASE.read_text(encoding='utf-8')

if 'app.register_blueprint(consent_bp)' in app_text:
    print('Consent demo already installed. Nothing changed.')
    raise SystemExit(0)

anchor = 'app = Flask(__name__)'
if anchor not in app_text:
    raise SystemExit('Could not find Flask app initialization. No files changed.')
nav_anchor = '''      </nav>'''
if nav_anchor not in base_text:
    raise SystemExit('Could not find sidebar navigation. No files changed.')

# Per-process ephemeral session key is adequate for a LOCAL SIMULATOR ONLY.
# Production deployments require persistent secrets and real login/authorization.
replacement = '''import secrets as _consent_demo_secrets
import importlib.util as _consent_demo_importlib
_consent_path = Path(__file__).resolve().parent / 'consent_demo.py'
_consent_spec = _consent_demo_importlib.spec_from_file_location('slt_consent_demo', _consent_path)
_consent_module = _consent_demo_importlib.module_from_spec(_consent_spec)
_consent_spec.loader.exec_module(_consent_module)
consent_bp = _consent_module.bp

app = Flask(__name__)
app.secret_key = _consent_demo_secrets.token_hex(32)  # LOCAL DEMO ONLY
app.register_blueprint(consent_bp)'''
new_app = app_text.replace(anchor, replacement, 1)
new_base = base_text.replace(nav_anchor,
    '''        <a class="nav-link {% if active == 'consent_demo' %}selected{% endif %}" href="{{ url_for('consent_demo.index') }}"><span class="nav-icon">◎</span> Consent simulator</a>
''' + nav_anchor, 1)

shutil.copy2(APP, APP.with_suffix('.py.step17.bak'))
shutil.copy2(BASE, BASE.with_suffix('.html.step17.bak'))
APP.write_text(new_app, encoding='utf-8')
BASE.write_text(new_base, encoding='utf-8')
print('Step 17 installed. Existing app.py and base.html backed up.')
print('Start website: python web_app\\app.py')
print('Visit: http://127.0.0.1:5000/consent-demo')
