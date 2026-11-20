(() => {
  const tabs = [...document.querySelectorAll(".os-tab")];
  const panels = new Map(tabs.map((tab) => [tab.dataset.os, document.getElementById(`panel-${tab.dataset.os}`)]));

  function select(os) {
    for (const tab of tabs) {
      const active = tab.dataset.os === os;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
      const panel = panels.get(tab.dataset.os);
      if (panel) panel.hidden = !active;
    }
  }

  for (const tab of tabs) {
    tab.addEventListener("click", () => select(tab.dataset.os));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      event.preventDefault();
      const index = tabs.indexOf(tab);
      const next = tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      next.focus();
      select(next.dataset.os);
    });
  }

  const toast = document.getElementById("toast");
  let toastTimer;
  function notify(message, isError = false) {
    if (!toast) return;
    clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.toggle("error", isError);
    toast.classList.add("visible");
    toastTimer = setTimeout(() => toast.classList.remove("visible"), 1800);
  }

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
  }

  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      try {
        await copy(button.dataset.copy);
        notify("Copied");
      } catch {
        notify("Could not copy", true);
      }
    });
  }

  // The public mirror publishes a tiny version file with each release; the
  // stable asset names are intentionally versionless.
  fetch("/releases/version.json", { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .then((release) => {
      if (!release || !release.version) return;
      document.querySelectorAll("[data-release-version]").forEach((node) => {
        node.textContent = release.version;
      });
    })
    .catch(() => {});
})();
