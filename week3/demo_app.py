"""
Week 3's real demo application - a genuinely small web app (a login page + a file upload
page) that the 4 CATALOG scenarios in auto_remediation.py actually break and fix, instead of
abstract JSON flags standing in for "an application". Registered into app.py as a Flask
Blueprint under /demo, so it runs in the exact same Cloud Run service/container as the main
RAG assistant - same disk, same process - which is what lets a "break" API call and a real
user hitting these pages agree on the same state.

Two of the 4 scenarios are purposeful toggles (app_down, login_ui_disabled) - built that way
on purpose, specifically to demonstrate event-based triggers, per the manager's ask. The
other two are genuine faults: login_cred_backend breaks a real backend config value the
login check depends on, and upload_permission does a real os.chmod() on the real folder
uploaded files are saved into (see mock_systems.py for both).
"""

from flask import Blueprint, redirect, render_template_string, request, url_for
from werkzeug.utils import secure_filename

try:
    # normal case: imported as part of the week3 package (by app.py or standalone_app.py)
    from . import mock_systems as ms
except ImportError:
    # also allow this to be imported when something ran a sibling file directly instead of
    # via the package (e.g. `cd week3/ && python3 standalone_app.py`) - mock_systems.py is
    # right next to this file either way
    import mock_systems as ms

demo_bp = Blueprint("demo", __name__, url_prefix="/demo")

# ---------- shared look, matching the main assistant UI's navy/teal palette ----------
_SHELL = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ page_title }} - Demo App</title>
<style>
  :root {
    --navy-900: #0f1c26; --navy-800: #162835; --navy-700: #1e3548;
    --teal: #00b3a4; --teal-dark: #009488; --amber: #f79009; --red: #d92d20;
    --blue: #2e90fa; --gray-bg: #eef1f4; --card-bg: #ffffff; --border: #dde3e8;
    --text-dark: #1b2733; --text-mid: #55636e; --text-light: #8a97a1;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; background: var(--gray-bg); color: var(--text-dark);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    display: flex; flex-direction: column; align-items: center;
  }
  #topbar {
    width: 100%; height: 52px; background: var(--navy-900); flex-shrink: 0;
    display: flex; align-items: center; justify-content: space-between; padding: 0 20px;
  }
  #topbar .brand { color: #f2f5f6; font-size: 14.5px; font-weight: 600; }
  #topbar .brand .crumb { color: #7f929c; font-weight: 400; margin-left: 6px; }
  #topbar a { color: #cfe4e2; font-size: 12.5px; text-decoration: none; margin-left: 14px; }
  #topbar a:hover { text-decoration: underline; }
  #wrap { width: 100%; max-width: 420px; padding: 48px 20px 60px; }
  .card {
    background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
    padding: 28px 26px; box-shadow: 0 1px 3px rgba(20,30,40,.06);
  }
  .card h1 { font-size: 18px; margin: 0 0 4px; }
  .card .sub { font-size: 13px; color: var(--text-mid); margin: 0 0 20px; }
  label { display: block; font-size: 12.5px; color: var(--text-mid); margin: 14px 0 5px; }
  input[type=text], input[type=password], input[type=file] {
    width: 100%; padding: 9px 11px; border: 1px solid var(--border); border-radius: 7px;
    font-size: 13.5px; background: #fbfcfd; color: var(--text-dark);
  }
  button {
    margin-top: 20px; width: 100%; padding: 10px 12px; border: none; border-radius: 7px;
    background: var(--teal); color: #06201d; font-size: 13.5px; font-weight: 600; cursor: pointer;
  }
  button:hover:not(:disabled) { background: var(--teal-dark); color: white; }
  button:disabled { background: #d7dde1; color: var(--text-light); cursor: not-allowed; }
  .banner {
    font-size: 12.5px; padding: 10px 12px; border-radius: 7px; margin-bottom: 14px;
    border: 1px solid transparent;
  }
  .banner.error { background: #fdefee; color: var(--red); border-color: #f7c8c3; }
  .banner.ok { background: #e6f9f5; color: var(--teal-dark); border-color: #b7ece3; }
  .banner.warn { background: #fef6e7; color: #93590a; border-color: #f7dfae; }
  .hint { font-size: 11.5px; color: var(--text-light); margin-top: 16px; line-height: 1.5; }
  .nav-row { display: flex; gap: 10px; margin-top: 16px; }
  .nav-row a { font-size: 12.5px; color: var(--blue); text-decoration: none; }
  .nav-row a:hover { text-decoration: underline; }

  /* ---------- demo controls tray - a plain, unappealing utility strip tucked at the
     bottom of the page, closed by default. Meant to read like an internal debug drawer a
     real ops team might leave behind for testing, not a feature of the application
     itself - never open on page load, never competing visually with the actual app. ---- */
  #dc-tray {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 50;
    background: var(--navy-900); border-top: 1px solid #0a141b;
    font-size: 12px; color: #b9c6cd;
  }
  #dc-handle {
    padding: 7px 20px; cursor: pointer; user-select: none;
    color: #7f929c; font-size: 11.5px; letter-spacing: .02em;
  }
  #dc-handle:hover { color: #b9c6cd; }
  #dc-handle::before { content: "\\25B8  "; }  /* small right-pointing triangle, flips when open */
  #dc-tray.open #dc-handle::before { content: "\\25BE  "; }
  .dc-panel {
    max-height: 0; overflow: hidden; transition: max-height .15s ease;
    padding: 0 20px;
  }
  #dc-tray.open .dc-panel { max-height: 240px; padding: 2px 20px 16px; }
  .dc-state {
    font-size: 11px; color: #8a97a1; background: rgba(255,255,255,.04);
    border: 1px solid #24404f; border-radius: 6px; padding: 7px 9px; margin-bottom: 10px;
    line-height: 1.6;
  }
  .dc-rows { display: flex; flex-direction: column; gap: 6px; max-width: 420px; }
  .dc-row { display: flex; align-items: center; gap: 8px; }
  .dc-label { flex: 1; font-size: 11.5px; color: #8a97a1; }
  .dc-btn {
    margin: 0; padding: 5px 12px; font-size: 11.5px; font-weight: 500; border-radius: 6px;
    flex-shrink: 0; width: 64px;
  }
  .dc-break { background: transparent; color: #e2908c; border: 1px solid #4a2c2a; }
  .dc-break:hover:not(:disabled) { background: var(--red); border-color: var(--red); color: white; }
  #dc-reset {
    margin-top: 10px; max-width: 420px; padding: 6px; font-size: 11.5px;
    background: transparent; color: #7f929c; border: 1px solid #2a4356; border-radius: 6px;
  }
  #dc-reset:hover { background: var(--navy-700); color: #cfe4e2; }
</style>
</head>
<body>
  <div id="topbar">
    <div class="brand">Demo App <span class="crumb">/ {{ page_title }}</span></div>
    <div>
      <a href="/demo/login">Login</a>
      <a href="/demo/upload">Upload</a>
      <a href="/">&larr; AIOps Assistant</a>
    </div>
  </div>
  <div id="wrap">
    <div class="card">{{ body|safe }}</div>
  </div>

  <div id="dc-tray">
    <div id="dc-handle">Demo controls</div>
    <div class="dc-panel">
      <div id="dc-state" class="dc-state">loading current state...</div>
      <div class="dc-rows">
        <div class="dc-row">
          <span class="dc-label">App Down</span>
          <button class="dc-btn dc-break" data-issue="app_down">Break</button>
        </div>
        <div class="dc-row">
          <span class="dc-label">Login UI disabled</span>
          <button class="dc-btn dc-break" data-issue="login_ui_disabled">Break</button>
        </div>
        <div class="dc-row">
          <span class="dc-label">Login backend broken</span>
          <button class="dc-btn dc-break" data-issue="login_cred_backend">Break</button>
        </div>
        <div class="dc-row">
          <span class="dc-label">Upload permission</span>
          <button class="dc-btn dc-break" data-issue="upload_permission">Break</button>
        </div>
      </div>
      <button id="dc-reset">Reset everything to healthy</button>
    </div>
  </div>

  <script>
  (function () {
    var tray = document.getElementById('dc-tray');
    var handle = document.getElementById('dc-handle');

    function refreshState() {
      fetch('/api/demo/state').then(function (r) { return r.json(); }).then(function (s) {
        var lines = [
          'app_down: ' + s.app_down,
          'login_ui_enabled: ' + s.login_ui_enabled,
          'login_cred_backend_ok: ' + s.login_cred_backend_ok,
          'upload_permission_ok: ' + s.upload_permission_ok,
        ];
        document.getElementById('dc-state').textContent = lines.join('  |  ');
      });
    }

    // closed on every page load, on purpose - this is a debug drawer, not part of the app
    handle.addEventListener('click', function () {
      tray.classList.toggle('open');
      if (tray.classList.contains('open')) { refreshState(); }
    });

    // Break-only, on purpose: fixing a scenario back to healthy is a human's job, done by
    // reporting the symptom through the chat with human-in-the-loop off (see
    // attempt_auto_remediation() in auto_remediation.py) - this tray never fixes anything
    // itself, it only breaks things for the demo.
    document.querySelectorAll('.dc-break').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        fetch('/api/demo/break', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ issue_id: btn.getAttribute('data-issue') }),
        }).then(function () {
          setTimeout(function () { location.reload(); }, 600);
        });
      });
    });
    var resetBtn = document.getElementById('dc-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        fetch('/api/demo/reset', { method: 'POST' }).then(function () { location.reload(); });
      });
    }
  })();
  </script>
</body>
</html>
"""

_DOWN_PAGE = """
<!doctype html><html><head><meta charset="utf-8">
<title>Service Unavailable</title>
<style>
  body { font-family: -apple-system, Roboto, Arial, sans-serif; background: #0f1c26; color: #f2f5f6;
         height: 100vh; margin: 0; display: flex; align-items: center; justify-content: center; }
  .box { text-align: center; }
  .box h1 { font-size: 22px; margin-bottom: 8px; }
  .box p { color: #b9c6cd; font-size: 13.5px; }
</style></head>
<body><div class="box">
  <h1>503 - Service Unavailable</h1>
  <p>The demo application is currently down.</p>
</div></body></html>
"""


def _render(page_title, body):
    return render_template_string(_SHELL, page_title=page_title, body=body)


@demo_bp.before_request
def _check_app_down():
    # the "app_down" scenario is meant to take the WHOLE demo app out, not just one page -
    # a before_request hook on the blueprint is the one place that's true for every route
    # under /demo without repeating the check in each view function
    if ms.get_state()["app_down"]:
        return _DOWN_PAGE, 503


@demo_bp.route("/")
def demo_home():
    return redirect(url_for("demo.login_page"))


# ---------------------------------------------------------------------------- login ----
_LOGIN_BODY = """
<h1>Sign in</h1>
<p class="sub">Demo login - username <code>demo_user</code></p>
{% if error %}<div class="banner error">{{ error }}</div>{% endif %}
{% if not ui_enabled %}
<div class="banner warn">The Sign In button is currently disabled by an application configuration issue.</div>
{% endif %}
<form method="post" action="/demo/login">
  <label>Username</label>
  <input type="text" name="username" autocomplete="username">
  <label>Password</label>
  <input type="password" name="password" autocomplete="current-password">
  <button type="submit" {{ "disabled" if not ui_enabled else "" }}>Sign In</button>
</form>
<p class="hint">Password: <code>Summer#2026</code></p>
"""

_LOGIN_SUCCESS_BODY = """
<h1>Signed in</h1>
<p class="sub">Welcome back, {{ username }}.</p>
<div class="banner ok">Login succeeded.</div>
<div class="nav-row"><a href="/demo/login">&larr; Sign out</a><a href="/demo/upload">Go to upload &rarr;</a></div>
"""


@demo_bp.route("/login", methods=["GET"])
def login_page():
    state = ms.get_state()
    return _render("Login", render_template_string(
        _LOGIN_BODY, error=request.args.get("error"), ui_enabled=state["login_ui_enabled"],
    ))


@demo_bp.route("/login", methods=["POST"])
def login_submit():
    state = ms.get_state()

    # server-side guard mirrors the disabled button - a direct POST shouldn't succeed
    # just because the UI check was client-side
    if not state["login_ui_enabled"]:
        return redirect(url_for("demo.login_page", error="Sign In is currently disabled."))

    # the real fault: check the backend config value BEFORE looking at what was typed,
    # exactly like a real app that can't reach its identity provider wouldn't be able to
    # tell a right password from a wrong one either
    if not ms.login_backend_url():
        return redirect(url_for(
            "demo.login_page",
            error="Unable to verify credentials - the credential backend is not configured (empty endpoint).",
        ))

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    if ms.DEMO_USERS.get(username) == password:
        return _render("Login", render_template_string(_LOGIN_SUCCESS_BODY, username=username))

    return redirect(url_for("demo.login_page", error="Invalid username or password."))


# --------------------------------------------------------------------------- upload ----
_UPLOAD_BODY = """
<h1>Upload a file</h1>
<p class="sub">Saves into the application's uploads folder.</p>
{% if error %}<div class="banner error">{{ error }}</div>{% endif %}
{% if ok %}<div class="banner ok">{{ ok }}</div>{% endif %}
<form method="post" action="/demo/upload" enctype="multipart/form-data">
  <label>File</label>
  <input type="file" name="file">
  <button type="submit">Upload</button>
</form>
<div class="nav-row"><a href="/demo/login">&larr; Back to login</a></div>
"""


@demo_bp.route("/upload", methods=["GET"])
def upload_page():
    return _render("Upload", render_template_string(
        _UPLOAD_BODY, error=request.args.get("error"), ok=request.args.get("ok"),
    ))


@demo_bp.route("/upload", methods=["POST"])
def upload_submit():
    file = request.files.get("file")
    if file is None or file.filename == "":
        return redirect(url_for("demo.upload_page", error="Choose a file first."))

    ms.ensure_upload_dir()
    dest = ms.UPLOAD_DIR / secure_filename(file.filename)
    try:
        file.save(str(dest))
    except PermissionError:
        # the real fault, surfacing exactly as it would in production: the OS itself
        # refused the write because of the folder's permission bits, not a canned message
        return redirect(url_for(
            "demo.upload_page",
            error="Upload failed - permission denied writing to the uploads folder.",
        ))

    return redirect(url_for("demo.upload_page", ok=f"Uploaded '{file.filename}' successfully."))
