const STATUS_LABEL = {
  draft: "Waiting for your review",
  confirmed: "Reminders running",
  finished: "Finished",
  cancelled: "Stopped",
};

async function loadConfig() {
  const config = await api("/api/config");
  document.getElementById("demo").hidden = !config.demo_mode;
  const select = document.getElementById("language");
  for (const language of config.languages) {
    select.append(el("option", { value: language.code, textContent: language.name }));
  }
  const zones = document.getElementById("timezones");
  if (Intl.supportedValuesOf) {
    for (const zone of Intl.supportedValuesOf("timeZone")) zones.append(el("option", { value: zone }));
  }
  document.getElementById("timezone").value = Intl.DateTimeFormat().resolvedOptions().timeZone;
}

async function loadList() {
  const rows = await api("/api/prescriptions");
  const list = document.getElementById("list");
  list.replaceChildren();
  document.getElementById("none").hidden = rows.length > 0;
  document.getElementById("list-wrap").hidden = rows.length === 0;
  for (const row of rows) {
    const isDraft = row.status === "draft";
    const link = el("a", {
      href: `${isDraft ? "/review" : "/summary"}?id=${row.id}`,
      textContent: isDraft ? "Review and confirm" : "View",
    });
    list.append(el("tr", {},
      el("td", {}, el("strong", { textContent: row.patient_first_name })),
      el("td", {}, el("span", { class: `pill pill-${row.status}`, textContent: STATUS_LABEL[row.status] })),
      el("td", { textContent: row.start_date || "" }),
      el("td", { textContent: new Date(row.created_at).toLocaleString() }),
      el("td", {}, link),
    ));
  }
}

// Show the chosen photo in the picker.
document.getElementById("photo").addEventListener("change", (event) => {
  const file = event.target.files[0];
  const preview = document.getElementById("preview");
  document.getElementById("drop").classList.toggle("has-photo", Boolean(file));
  document.getElementById("file-name").textContent = file ? file.name : "or drop it here";
  preview.hidden = !file;
  if (file) preview.src = URL.createObjectURL(file);
});

document.getElementById("upload").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("submit");
  button.disabled = true;
  document.getElementById("busy").hidden = false;
  showBanner("error", "");
  try {
    const result = await api("/api/prescriptions", { method: "POST", body: new FormData(event.target) });
    window.location.href = `/review?id=${result.id}`;
  } catch (error) {
    showBanner("error", error.message, true);
    button.disabled = false;
    document.getElementById("busy").hidden = true;
  }
});

loadConfig().then(loadList).catch((error) => showBanner("error", error.message, true));
