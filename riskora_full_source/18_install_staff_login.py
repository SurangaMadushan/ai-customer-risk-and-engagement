"""Apply Step 18 local test authentication to your EXISTING Step 17 Flask site.

Copy the Step 18 files to SLT_Payment_Project, then run:
  python 18_install_staff_login.py

Idempotent. Makes backups. Does not overwrite app.py with a different website.
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
APP = ROOT / 'web_app' / 'app.py'
BASE = ROOT / 'web_app' / 'templates' / 'base.html'
INDEX = ROOT / 'web_app' / 'templates' / 'index.html'
AUTH = ROOT / 'web_app' / 'staff_auth.py'
LOGIN = ROOT / 'web_app' / 'templates' / 'staff_login.html'
START = '# >>> STEP 18 STAFF AUTH >>>'

for path in (APP, BASE, INDEX, AUTH, LOGIN):
    if not path.is_file():
        raise SystemExit(f'Missing {path.relative_to(ROOT)}. Extract the Step 18 files first.')

app_text = APP.read_text(encoding='utf-8')
base_text = BASE.read_text(encoding='utf-8')
index_text = INDEX.read_text(encoding='utf-8')
if START in app_text:
    print('Step 18 is already installed. No changes needed.')
    raise SystemExit(0)

anchor = '@app.before_request\ndef check_url_size():'
if anchor not in app_text:
    raise SystemExit('Your app.py structure is different. No files changed. Ask for a compatible installer.')
if 'app.register_blueprint(consent_bp)' not in app_text:
    raise SystemExit('Step 17 consent module not installed in app.py. Install Step 17 first.')

nav_anchor = '      <div class="sidebar-foot">'
if nav_anchor not in base_text:
    raise SystemExit('Could not find sidebar in base.html. No files changed.')

patch = '''# >>> STEP 18 STAFF AUTH >>>
from staff_auth import configure_staff_auth as _step18_configure_staff_auth
_step18_configure_staff_auth(app)
# <<< STEP 18 STAFF AUTH <<<

'''
new_app = app_text.replace(anchor, patch + anchor, 1)
role_nav = '''        {% if staff_identity and staff_identity.role in ['admin','analyst'] %}'''
new_base = base_text
# Only display protected menu items when the user's role allows their endpoints.
for endpoint in ('customers', 'creditworthiness', 'collections'):
    lines = new_base.splitlines(keepends=True)
    changed = False
    for i, ln in enumerate(lines):
        if 'href="{{ url_for(\'' + endpoint + '\') }}"' in ln:
            lines[i] = '        {% if staff_identity and staff_identity.role in [\'admin\',\'analyst\'] %}\n' + ln + '        {% endif %}\n'
            changed = True
            break
    if not changed:
        raise SystemExit(f'Could not locate {endpoint} navigation. No files changed.')
    new_base = ''.join(lines)
# Conditionally show consent-demo link (installed by Step 17).
lines = new_base.splitlines(keepends=True)
for i, ln in enumerate(lines):
    if "url_for('consent_demo.index')" in ln:
        lines[i] = "        {% if staff_identity and staff_identity.role in ['admin','analyst'] %}\n" + ln + '        {% endif %}\n'
        break
new_base = ''.join(lines)

staff_box = '''      {% if staff_identity %}
      <div class="sidebar-foot" style="border-top:1px solid #dae3ef;padding-top:14px;">
        <div style="font-weight:700;word-break:break-word;">{{ staff_identity.username }}</div>
        <small>Staff role: {{ staff_identity.role|capitalize }}</small>
        <form method="post" action="{{ url_for('staff_auth.logout') }}" style="margin-top:10px;">
          <input type="hidden" name="csrf_token" value="{{ staff_logout_csrf }}">
          <button type="submit" style="padding:7px 12px;border:1px solid #b7c7d9;border-radius:7px;cursor:pointer;background:#fff;color:#143e67;">Sign out</button>
        </form>
      </div>
      {% endif %}
'''
new_base = new_base.replace(nav_anchor, staff_box + nav_anchor, 1)
# Make the global search available only to permitted roles. Security is enforced server-side.
form_start = '        <form class="top-search"'
pos = new_base.find(form_start)
if pos != -1:
    end = new_base.find('</form>', pos)
    if end == -1:
        raise SystemExit('Could not modify global search. No files changed.')
    end += len('</form>')
    new_base = (new_base[:pos] + "        {% if staff_identity and staff_identity.role in ['admin','analyst'] %}\n" +
                new_base[pos:end] + '\n        {% endif %}' + new_base[end:])

# The overview contains individual priority accounts; a viewer must see only aggregated summaries.
priority_anchor = '<section class="panel table-panel"><div class="panel-title"><div><h2>Priority follow-up accounts'
if priority_anchor not in index_text or '</section>\n{% endblock %}' not in index_text:
    raise SystemExit('Unexpected index.html structure. No files changed.')
new_index = index_text.replace(priority_anchor,
    "{% if staff_identity and staff_identity.role in ['admin','analyst'] %}\n" + priority_anchor, 1)
endpos = new_index.rfind('</section>\n{% endblock %}')
new_index = new_index[:endpos] + '</section>\n{% endif %}\n{% endblock %}' + new_index[endpos + len('</section>\n{% endblock %}'):]
# Hide direct links to account-level data from viewers (backend still enforces all roles).
links = [
    '<a class="btn btn-primary" href="{{ url_for(\'customers\') }}">Find a customer <span>↗</span></a>',
    '<a href="{{ url_for(\'collections\') }}">View all →</a>',
    '<a href="{{ url_for(\'customers\') }}">Explore →</a>',
]
for link in links:
    if link not in new_index:
        raise SystemExit('Unexpected overview links. No files changed.')
    new_index = new_index.replace(link, "{% if staff_identity and staff_identity.role in ['admin','analyst'] %}" + link + '{% endif %}', 1)

shutil.copy2(APP, APP.with_suffix('.py.step18.bak'))
shutil.copy2(BASE, BASE.with_suffix('.html.step18.bak'))
shutil.copy2(INDEX, INDEX.with_suffix('.html.step18.bak'))
APP.write_text(new_app, encoding='utf-8')
BASE.write_text(new_base, encoding='utf-8')
INDEX.write_text(new_index, encoding='utf-8')

ignore = ROOT / '.gitignore'
needed = ['# SLT Step 17/18 local-only secrets and session databases',
          'data/staff_auth.sqlite3', 'data/staff_auth_secret',
          'data/consent_demo.sqlite3']
existing = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
missing = [ln for ln in needed if ln not in existing]
if missing:
    with ignore.open('a', encoding='utf-8') as file:
        if existing and not existing.endswith('\n'):
            file.write('\n')
        file.write('\n'.join(missing) + '\n')
print('Step 18 installed: staff login, role checks, logout and session settings.')
print('Original app.py and templates saved as *.step18.bak files.')
print('Next: python 18_create_staff_user.py --username admin1 --role admin')
