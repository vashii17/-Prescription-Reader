const prescriptionId = new URLSearchParams(window.location.search).get("id");
const FOOD = { before: "Before food", after: "After food", any: "Any time", unknown: "Not stated" };
const HEADLINE = {
  confirmed: "Reminders are running",
  finished: "The course is finished",
  cancelled: "This schedule was stopped",
};
let timer = null;

function fill(id, rows) {
  document.getElementById(id).replaceChildren(...rows);
}

async function refresh() {
  const data = await api(`/api/prescriptions/${prescriptionId}`);
  const prescription = data.prescription;
  if (prescription.status === "draft") {
    window.location.replace(`/review?id=${prescriptionId}`);
    return;
  }
  const zone = prescription.timezone;
  document.getElementById("patient").textContent = prescription.patient_first_name;
  document.getElementById("demo").hidden = !prescription.demo_mode;
  document.getElementById("headline").textContent = HEADLINE[prescription.status];
  for (const status of ["taken", "skipped", "missed", "pending"]) {
    document.getElementById(`count-${status}`).textContent = data.counts[status];
  }

  const running = prescription.status === "confirmed";
  document.getElementById("cancel").hidden = !running;
  let next = "";
  if (running && data.workflow) {
    next = data.workflow.awaiting_response
      ? "Waiting for an answer to the current reminder."
      : data.workflow.next_dose_at ? `Next dose: ${formatWhen(data.workflow.next_dose_at, zone)}.` : "";
  } else if (running) {
    next = "The worker is not answering. Reminders will continue when it is running again.";
  }
  document.getElementById("next").textContent = next;

  fill("medicines", data.medicines.map((medicine) => el("tr", {},
    el("td", { textContent: medicine.name }),
    el("td", { textContent: medicine.strength }),
    el("td", { textContent: medicine.dose_amount }),
    el("td", { textContent: medicine.times_of_day.join(", ") }),
    el("td", { textContent: FOOD[medicine.with_food] }),
    el("td", { textContent: medicine.duration_days }),
    el("td", { textContent: medicine.notes }),
  )));

  fill("doses", data.doses.map((dose) => el("tr", {},
    el("td", { textContent: formatWhen(dose.due_at, zone) }),
    el("td", { textContent: [dose.dose_amount, dose.name, dose.strength].filter(Boolean).join(" ") }),
    el("td", {}, dose.status === "pending" ? "" : el("span", { class: `pill pill-${dose.status}`, textContent: dose.status })),
    el("td", { textContent: dose.reminders_sent || "" }),
  )));

  fill("events", data.events.map((event) => el("tr", {},
    el("td", { textContent: formatWhen(event.due_at, zone) }),
    el("td", { textContent: event.kind === "reminder" ? `Reminder ${event.attempt}` : `Recorded: ${event.outcome}` }),
    el("td", { textContent: event.scheduled_at && event.attempt === 1 ? formatClock(event.scheduled_at) : "" }),
    el("td", { textContent: formatClock(event.created_at) }),
    el("td", {}, event.kind !== "reminder" ? "" : el("span", {
      class: event.has_audio ? "pill pill-voice" : "pill",
      textContent: event.has_audio ? "ElevenLabs" : "Browser speech",
    })),
  )));

  if (!running && timer) { clearInterval(timer); timer = null; }
}

document.getElementById("cancel").addEventListener("click", async () => {
  if (!window.confirm("Stop all reminders for this prescription?")) return;
  try {
    await api(`/api/prescriptions/${prescriptionId}/cancel`, { method: "POST" });
    await refresh();
  } catch (error) {
    showBanner("error", error.message, true);
  }
});

function safeRefresh() {
  refresh().then(() => showBanner("error", "")).catch((error) => showBanner("error", error.message, true));
}

safeRefresh();
timer = setInterval(safeRefresh, 3000);
