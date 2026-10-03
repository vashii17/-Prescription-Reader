// Reminder screen. Reminders arrive over server-sent events; answers go back as POSTs.

const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";
const player = document.getElementById("player");
const statusLine = document.getElementById("status");
let current = null;   // the reminder on screen
let lastEventId = 0;  // highest reminder event already shown, so a reconnect does not repeat it

function show(id, visible) {
  document.getElementById(id).hidden = !visible;
  if (id === "reminder") document.body.classList.toggle("due", visible);
}

// Browser speech: used when there is no ElevenLabs audio, or when it fails to play.
function speakWithBrowser(reminder) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(reminder.text);
  utterance.lang = reminder.lang;
  utterance.rate = 0.9;
  window.speechSynthesis.speak(utterance);
}

function speak(reminder) {
  if (!reminder.audio_url) { speakWithBrowser(reminder); return; }
  player.onerror = () => speakWithBrowser(reminder);
  player.src = reminder.audio_url;
  player.play().catch(() => speakWithBrowser(reminder));
}

function stopSpeaking() {
  player.pause();
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
}

function clearReminder(message) {
  current = null;
  stopSpeaking();
  show("reminder", false);
  show("waiting-screen",true);
  statusLine.textContent = message || "";
}

function sameDose(a, b) {
  return a && b && a.prescription_id === b.prescription_id && a.due_at === b.due_at;
}

function onReminder(event) {
  const id = Number(event.lastEventId);
  if (id <= lastEventId) return;
  lastEventId = id;
  current = JSON.parse(event.data);
  document.getElementById("reminder-text").textContent = current.text;
  show("waiting-screen",false);
  show("reminder", true);
  statusLine.textContent = "";
  speak(current);
}

function onResolved(event) {
  const resolved = JSON.parse(event.data);
  if (!sameDose(current, resolved)) return;
  const message = { taken: "Recorded: taken.", skipped: "Recorded: skipped.", missed: "" }[resolved.outcome];
  clearReminder(message);
}

// Sent at the start of every connection: the doses still waiting for an answer.
function onSync(event) {
  const open = JSON.parse(event.data).open;
  if (current && !open.some(([prescriptionId, dueAt]) => prescriptionId === current.prescription_id && dueAt === current.due_at)) {
    clearReminder("");
  }
}

async function respond(action) {
  if (!current) return;
  const answered = current;
  stopSpeaking();
  statusLine.textContent = "";
  try {
    const response = await fetch("/api/doses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prescription_id: answered.prescription_id, due_at: answered.due_at, action, minutes: 10 }),
    });
    if (!response.ok) throw new Error();
    if (action === "snooze" && sameDose(current, answered)) clearReminder("I will remind you again shortly.");
  } catch (_) {
    statusLine.textContent = "That did not go through. Please press the button again.";
  }
}

document.getElementById("taken").addEventListener("click", () => respond("taken"));
document.getElementById("skip").addEventListener("click", () => respond("skipped"));
document.getElementById("later").addEventListener("click", () => respond("snooze"));

document.getElementById("start").addEventListener("click", () => {
  // Browsers only allow sound after a tap. Playing silence here unlocks later reminders.
  player.src = SILENCE;
  player.play().catch(() => {});
  if ("speechSynthesis" in window) window.speechSynthesis.speak(new SpeechSynthesisUtterance(""));

  show("start-screen", false);
  show("waiting-screen",true);

  const stream = new EventSource("/api/reminders");
  stream.addEventListener("sync", onSync);
  stream.addEventListener("reminder", onReminder);
  stream.addEventListener("resolved", onResolved);
  stream.onerror = () => { statusLine.textContent = "Reconnecting…"; };
  stream.onopen = () => { if (statusLine.textContent === "Reconnecting…") statusLine.textContent = ""; };
});
