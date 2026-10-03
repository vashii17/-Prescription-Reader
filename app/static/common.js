// Small helpers shared by the caregiver pages.

const DISCLAIMER = "Check with your doctor or pharmacist. This tool only repeats the schedule you confirmed.";

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = value;
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body = null;
  try { body = await response.json(); } catch (_) { /* no JSON body */ }
  if (!response.ok) {
    let message = body && body.detail ? body.detail : `Request failed (${response.status})`;
    if (Array.isArray(message)) {
      message = message.map((item) => `${item.loc.slice(1).join(" > ")}: ${item.msg}`).join("; ");
    }
    throw new Error(message);
  }
  return body;
}

function showBanner(id, message, isError = false) {
  const node = document.getElementById(id);
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.hidden = !message;
}

function formatWhen(isoString, timeZone) {
  return new Intl.DateTimeFormat(undefined, {
    timeZone, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  }).format(new Date(isoString));
}

function formatClock(isoString) {
  return new Date(isoString).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
