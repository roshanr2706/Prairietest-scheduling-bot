async function refreshLogs() {
  const table = document.getElementById("events");
  if (!table) return;
  try {
    const r = await fetch("/api/events");
    const data = await r.json();
    table.innerHTML = "<tr><th>Time</th><th>Level</th><th>Message</th></tr>" +
      data.events.map(e => `<tr><td>${e.ts}</td><td>${e.level}</td><td>${e.message}</td></tr>`).join("");
  } catch (e) { /* ignore transient errors */ }
}
if (document.getElementById("events")) {
  refreshLogs();
  setInterval(refreshLogs, 5000);
}
