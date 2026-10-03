const prescriptionId = new URLSearchParams(window.location.search).get("id");
const SLOTS = ["morning", "afternoon", "evening", "night"];
const FOOD = [["unknown", "Not stated"], ["before", "Before food"], ["after", "After food"], ["any", "Any time"]];
const MARK = { low: "unsure", medium: "check" };

// Colour a cell by the model's confidence, or as empty. Editing it clears the mark.
function mark(cell, confidence, isEmpty) {
  cell.classList.add(isEmpty ? "empty" : MARK[confidence] || "plain");
  cell.addEventListener("input", () => cell.classList.remove("empty", "unsure", "check"), { once: true });
  cell.addEventListener("change", () => cell.classList.remove("empty", "unsure", "check"), { once: true });
}

const LABELS = {
  name: "Medicine", strength: "Strength", dose_amount: "Amount per dose",
  duration_days: "Days", notes: "Notes",
};

function textCell(field, value, confidence, required, type = "text") {
  const input = el("input", { type, value: value ?? "" });
  input.dataset.field = field;
  input.setAttribute("aria-label", LABELS[field]);
  if (type === "number") { input.min = 1; input.max = 365; }
  const cell = el("td", { class: `f-${field}`, "data-label": LABELS[field] }, input);
  const isEmpty = value === null || value === undefined || value === "";
  mark(cell, confidence, isEmpty && required);
  return cell;
}

function addRow(medicine) {
  const confidence = medicine.confidence || {};
  const row = el("tr");

  const times = el("td", { class: "times", "data-label": "When" });
  const chips = el("div", { class: "chips" });
  for (const slot of SLOTS) {
    const box = el("input", { type: "checkbox", value: slot, checked: (medicine.times_of_day || []).includes(slot) });
    chips.append(el("label", { class: "chip" }, box, el("span", { textContent: slot })));
  }
  const warning = el("div", { class: "row-warning" });
  times.append(chips, warning);
  mark(times, confidence.times_of_day, (medicine.times_of_day || []).length === 0);
  const frequency = medicine.frequency_per_day;
  const checkFrequency = () => {
    const chosen = times.querySelectorAll("input:checked").length;
    warning.textContent = frequency && frequency !== chosen
      ? `The prescription was read as ${frequency} a day; ${chosen} selected.` : "";
  };
  times.addEventListener("change", checkFrequency);
  checkFrequency();

  const food = el("select");
  food.dataset.field = "with_food";
  for (const [value, label] of FOOD) food.append(el("option", { value, textContent: label }));
  food.value = medicine.with_food || "unknown";
  food.setAttribute("aria-label", "Food");
  const foodCell = el("td", { class: "f-with_food", "data-label": "Food" }, food);
  mark(foodCell, confidence.with_food, false);

  const remove = el("button", { type: "button", class: "danger quiet", textContent: "Remove" });
  remove.addEventListener("click", () => row.remove());

  row.append(
    textCell("name", medicine.name, confidence.name, true),
    textCell("strength", medicine.strength, confidence.strength, false),
    textCell("dose_amount", medicine.dose_amount, confidence.dose_amount, true),
    times,
    foodCell,
    textCell("duration_days", medicine.duration_days, confidence.duration_days, true, "number"),
    textCell("notes", medicine.notes, confidence.notes, false),
    el("td", { class: "remove" }, remove),
  );
  document.getElementById("medicines").append(row);
}

function readRows() {
  return [...document.querySelectorAll("#medicines tr")].map((row) => {
    const field = (name) => row.querySelector(`[data-field="${name}"]`).value.trim();
    return {
      name: field("name"),
      strength: field("strength"),
      dose_amount: field("dose_amount"),
      times_of_day: [...row.querySelectorAll("td.times input:checked")].map((box) => box.value),
      with_food: field("with_food"),
      duration_days: Number(field("duration_days")),
      notes: field("notes"),
    };
  });
}

function validate(medicines) {
  if (medicines.length === 0) return "Add at least one medicine.";
  for (const [index, medicine] of medicines.entries()) {
    const label = medicine.name || `Row ${index + 1}`;
    if (!medicine.name) return `Row ${index + 1}: enter the medicine name.`;
    if (!medicine.dose_amount) return `${label}: enter the amount per dose.`;
    if (medicine.times_of_day.length === 0) return `${label}: choose at least one time of day.`;
    if (!Number.isInteger(medicine.duration_days) || medicine.duration_days < 1) return `${label}: enter the number of days.`;
  }
  return "";
}

async function load() {
  const [config, data] = await Promise.all([api("/api/config"), api(`/api/prescriptions/${prescriptionId}`)]);
  if (data.prescription.status !== "draft") {
    window.location.replace(`/summary?id=${prescriptionId}`);
    return;
  }
  document.getElementById("demo").hidden = !config.demo_mode;
  document.getElementById("photo").src = `/api/prescriptions/${prescriptionId}/image`;
  document.getElementById("photo-link").href = `/api/prescriptions/${prescriptionId}/image`;
  document.getElementById("patient").textContent = data.prescription.patient_first_name;
  document.getElementById("timezone").textContent = data.prescription.timezone;
  for (const slot of SLOTS) document.getElementById(`time-${slot}`).value = config.default_slot_times[slot];
  document.getElementById("start-date").value = new Intl.DateTimeFormat("en-CA", {
    timeZone: data.prescription.timezone,
  }).format(new Date());

  const extraction = data.extraction || {};
  if (extraction.status === "unavailable") {
    showBanner("notice", "The local model could not be reached, so nothing was read. Type the schedule in by hand from the photo.", true);
  } else if (extraction.status === "invalid") {
    showBanner("notice", "The model could not read this photo reliably. Type the schedule in by hand from the photo.", true);
  } else if (!(extraction.medicines || []).length) {
    showBanner("notice", "No medicines were found in the photo. Add them by hand.", true);
  }
  const medicines = extraction.medicines && extraction.medicines.length ? extraction.medicines : [{}];
  medicines.forEach(addRow);
}

document.getElementById("add").addEventListener("click", () => addRow({}));

document.getElementById("confirm").addEventListener("click", async () => {
  const medicines = readRows();
  const problem = validate(medicines);
  if (problem) { showBanner("error", problem, true); return; }
  const slotTimes = Object.fromEntries(SLOTS.map((slot) => [slot, document.getElementById(`time-${slot}`).value]));
  const button = document.getElementById("confirm");
  button.disabled = true;
  showBanner("error", "");
  try {
    await api(`/api/prescriptions/${prescriptionId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        medicines,
        slot_times: slotTimes,
        start_date: document.getElementById("start-date").value,
      }),
    });
    window.location.href = `/summary?id=${prescriptionId}`;
  } catch (error) {
    showBanner("error", error.message, true);
    button.disabled = false;
  }
});

load().catch((error) => showBanner("error", error.message, true));
