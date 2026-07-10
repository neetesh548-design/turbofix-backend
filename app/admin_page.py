"""The internal TurboFix-team admin console, served as one self-contained HTML page by
GET /admin. Kept as a Python string (no static-file host, no build step) so the whole
console ships with the backend and works the moment the backend is deployed. It talks
to /admin/login, GET /admin/companies, and POST /admin/companies/{code}."""

ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TurboFix — Team Admin</title>
<style>
  * { box-sizing: border-box; margin: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f141b; color: #e8ecf1; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 28px 18px; }
  h1 { font-size: 22px; font-weight: 600; }
  .tag { display:inline-block; background:#12351f; color:#8fe0ab; font-size:11px;
         padding:2px 8px; border-radius:9px; margin-left:8px; vertical-align:middle; }
  .sub { color:#93a0b4; font-size:14px; margin:6px 0 22px; }
  .card { background:#18212e; border:1px solid #283445; border-radius:12px; padding:22px; max-width:400px; }
  label { display:block; font-size:13px; color:#93a0b4; margin-bottom:6px; }
  input { width:100%; padding:11px 13px; font-size:15px; border-radius:8px;
          border:1px solid #283445; background:#0f141b; color:#e8ecf1; }
  button { border:0; border-radius:8px; cursor:pointer; font-weight:600; }
  .btn-primary { padding:11px 16px; background:#ff7a1a; color:#1a1208; font-size:15px; }
  .btn-primary:hover { background:#ff8c3a; }
  .err { color:#ff8f8f; font-size:13px; margin-top:10px; min-height:16px; }
  table { width:100%; border-collapse:collapse; margin-top:8px; }
  th, td { text-align:left; padding:11px 12px; font-size:14px; border-bottom:1px solid #232f3f; }
  th { color:#93a0b4; font-weight:500; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .pill { font-size:12px; padding:3px 9px; border-radius:10px; font-weight:600; }
  .ok { background:#12351f; color:#8fe0ab; }
  .pending { background:#3a2a12; color:#ffce8f; }
  .over { color:#ff8f8f; font-weight:600; }
  .qty { width:70px; padding:6px 8px; font-size:14px; }
  .row-actions { display:flex; gap:8px; align-items:center; }
  .btn-sm { padding:6px 11px; font-size:13px; background:#283445; color:#e8ecf1; }
  .btn-sm:hover { background:#33425a; }
  .btn-approve { background:#1c6b3a; color:#eafff1; }
  .btn-approve:hover { background:#238049; }
  .topbar { display:flex; justify-content:space-between; align-items:baseline; }
  .muted { color:#6b7789; font-size:12px; }
  #adminApp { display:none; }
</style>
</head>
<body>
<div class="wrap">
  <div class="topbar">
    <h1>TurboFix team admin <span class="tag">internal</span></h1>
    <a href="#" id="logout" class="muted" style="display:none">sign out</a>
  </div>
  <p class="sub">Approve companies and set how many machines each has paid to onboard.</p>

  <div id="loginBox" class="card">
    <label for="pw">Admin password</label>
    <input type="password" id="pw" placeholder="team password" autocomplete="current-password">
    <div style="margin-top:14px"><button class="btn-primary" id="loginBtn" style="width:100%">Sign in</button></div>
    <div class="err" id="loginErr"></div>
  </div>

  <div id="adminApp">
    <details class="card" style="max-width: 100%; margin-bottom: 24px; border: 1px solid #283445; background: #18212e; padding: 18px; border-radius: 12px;">
      <summary style="cursor:pointer; font-weight:600; outline:none; user-select:none;">+ Onboard New Company</summary>
      <form id="onboardForm" style="margin-top: 18px; display: grid; grid-template-columns: 1fr 1fr; gap: 14px;">
        <div>
          <label>Company Code</label>
          <input type="text" id="onboardCode" placeholder="e.g. XYZ1" required>
        </div>
        <div>
          <label>Company Name</label>
          <input type="text" id="onboardName" placeholder="e.g. XYZ Engineering" required>
        </div>
        <div>
          <label>Owner Name</label>
          <input type="text" id="onboardOwnerName" placeholder="e.g. John Doe" required>
        </div>
        <div>
          <label>Owner Phone</label>
          <input type="text" id="onboardPhone" placeholder="e.g. +919820012345" required>
        </div>
        <div>
          <label>Owner Email</label>
          <input type="email" id="onboardEmail" placeholder="e.g. owner@xyz.com" required>
        </div>
        <div>
          <label>Owner Password</label>
          <input type="password" id="onboardPassword" minlength="8" required>
        </div>
        <div>
          <label>Initial Machine Quota</label>
          <input type="number" id="onboardQuota" value="5" min="1" required>
        </div>
        <div style="grid-column: span 2; margin-top: 10px;">
          <button type="submit" class="btn-primary" id="onboardSubmitBtn" style="padding: 10px 18px; border-radius: 8px;">Onboard & Approve Company</button>
          <div class="err" id="onboardErr" style="margin-top: 8px;"></div>
        </div>
      </form>
    </details>

    <div style="overflow-x:auto;">
      <table id="companiesTable">
        <thead><tr>
          <th>Company</th><th>Code</th><th>Admin phone</th>
          <th>Machines</th><th>Quota</th><th>Payment Confirmation</th><th>Status</th><th></th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
    <p class="muted" style="margin-top:14px" id="statusMsg"></p>
  </div>
</div>

<script>
const API = location.origin;
let token = sessionStorage.getItem("tfAdminToken") || "";

const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  opts.headers = Object.assign({"Content-Type": "application/json"}, opts.headers || {},
                               token ? {"Authorization": "Bearer " + token} : {});
  const resp = await fetch(API + path, opts);
  if (resp.status === 401) { showLogin(); throw new Error("unauthorized"); }
  return resp;
}

function showLogin() {
  token = ""; sessionStorage.removeItem("tfAdminToken");
  $("adminApp").style.display = "none";
  $("logout").style.display = "none";
  $("loginBox").style.display = "block";
}
function showApp() {
  $("loginBox").style.display = "none";
  $("adminApp").style.display = "block";
  $("logout").style.display = "inline";
  loadCompanies();
}

async function login() {
  $("loginErr").textContent = "";
  try {
    const resp = await fetch(API + "/admin/login", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({password: $("pw").value}),
    });
    if (!resp.ok) { $("loginErr").textContent = "Incorrect admin password."; return; }
    token = (await resp.json()).access_token;
    sessionStorage.setItem("tfAdminToken", token);
    $("pw").value = "";
    showApp();
  } catch (e) { $("loginErr").textContent = "Error: " + e.message; }
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

async function loadCompanies() {
  const resp = await api("/admin/companies");
  const companies = await resp.json();
  $("rows").innerHTML = companies.map(c => {
    const over = c.machines_used > c.machine_quota;
    const status = c.approved
      ? '<span class="pill ok">approved</span>'
      : '<span class="pill pending">pending</span>';
    const used = `<span class="${over ? "over" : ""}">${c.machines_used}</span>`;
    
    // View Payment Link
    const paymentCell = c.has_payment_screenshot
      ? `<a href="/admin/companies/${encodeURIComponent(c.company_code)}/payment-screenshot?token=${encodeURIComponent(token)}" target="_blank" style="color:#ff7a1a; font-weight:600; text-decoration:none;">View Confirmation</a>`
      : '<span class="muted">None</span>';

    return `<tr data-code="${esc(c.company_code)}">
      <td>${esc(c.company_name)}</td>
      <td>${esc(c.company_code)}</td>
      <td>${esc(c.admin_contact_phone)}</td>
      <td>${used}</td>
      <td><input type="number" min="0" class="qty" value="${c.machine_quota}"></td>
      <td>${paymentCell}</td>
      <td>${status}</td>
      <td><div class="row-actions">
        <button class="btn-sm saveQuota">Save quota</button>
        <button class="btn-sm ${c.approved ? "" : "btn-approve"} toggleApprove">${c.approved ? "Unapprove" : "Approve"}</button>
      </div></td>
    </tr>`;
  }).join("");
  bindRowActions();
}

function bindRowActions() {
  document.querySelectorAll("#rows tr").forEach(tr => {
    const code = tr.dataset.code;
    tr.querySelector(".saveQuota").onclick = () =>
      patch(code, {machine_quota: parseInt(tr.querySelector(".qty").value, 10) || 0});
    const btn = tr.querySelector(".toggleApprove");
    btn.onclick = () => patch(code, {approved: btn.textContent === "Approve"});
  });
}

async function patch(code, body) {
  $("statusMsg").textContent = "Saving…";
  try {
    const resp = await api("/admin/companies/" + encodeURIComponent(code), {
      method: "POST", body: JSON.stringify(body),
    });
    if (!resp.ok) { $("statusMsg").textContent = "Save failed."; return; }
    $("statusMsg").textContent = "Saved " + code + " at " + new Date().toLocaleTimeString();
    loadCompanies();
  } catch (e) { $("statusMsg").textContent = "Error: " + e.message; }
}

$("onboardForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const errEl = $("onboardErr");
  errEl.textContent = "";
  const btn = $("onboardSubmitBtn");
  btn.disabled = true;
  
  try {
    const resp = await api("/admin/companies", {
      method: "POST",
      body: JSON.stringify({
        company_code: $("onboardCode").value.trim(),
        company_name: $("onboardName").value.trim(),
        admin_contact_phone: $("onboardPhone").value.trim(),
        owner_name: $("onboardOwnerName").value.trim(),
        owner_email: $("onboardEmail").value.trim(),
        owner_password: $("onboardPassword").value,
        machine_quota: parseInt($("onboardQuota").value, 10),
      })
    });
    if (!resp.ok) {
      let msg = "Onboarding failed.";
      try { msg = (await resp.json()).detail || msg; } catch(_) {}
      throw new Error(msg);
    }
    $("onboardForm").reset();
    loadCompanies();
  } catch (err) {
    errEl.textContent = err.message;
  } finally {
    btn.disabled = false;
  }
});

$("loginBtn").onclick = login;
$("pw").addEventListener("keypress", e => { if (e.key === "Enter") login(); });
$("logout").onclick = (e) => { e.preventDefault(); showLogin(); };

if (token) showApp(); else showLogin();
</script>
</body>
</html>
"""
