// Format a SQLite UTC timestamp ("YYYY-MM-DD HH:MM:SS") into the viewer's local time.
function fmtUtc(s) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  return isNaN(d) ? s : d.toLocaleString();
}
// Format an ISO timestamp (with offset) into the viewer's local time.
function fmtIso(s) {
  if (!s) return "";
  const d = new Date(s);
  return isNaN(d) ? s : d.toLocaleString();
}
function levelBadge(level) {
  const map = { error: "danger", warn: "warning", info: "secondary", booked: "success" };
  const cls = map[level] || "secondary";
  return `<span class="badge text-bg-${cls}">${level}</span>`;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

// Convert any static timestamp cells rendered by the server.
document.querySelectorAll("[data-ts-utc]").forEach(e => { e.textContent = fmtUtc(e.getAttribute("data-ts-utc")); });
document.querySelectorAll("[data-ts-iso]").forEach(e => { e.textContent = fmtIso(e.getAttribute("data-ts-iso")); });

async function refreshLogs() {
  const tbody = document.getElementById("events-body");
  if (!tbody) return;
  try {
    const r = await fetch("/api/events");
    const data = await r.json();
    if (!data.events.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="text-center text-secondary py-4">No activity yet.</td></tr>';
      return;
    }
    tbody.innerHTML = data.events.map(e =>
      `<tr><td class="text-nowrap small text-secondary">${fmtUtc(e.ts)}</td>` +
      `<td>${levelBadge(e.level)}</td>` +
      `<td>${escapeHtml(e.message)}</td></tr>`
    ).join("");
  } catch (e) { /* ignore transient errors */ }
}
if (document.getElementById("events-body")) {
  refreshLogs();
  setInterval(refreshLogs, 5000);
}
