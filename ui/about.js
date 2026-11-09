/* The title and description card of a game (in the reader) or a tournament (on its stats page).
   Shows the saved note and, when the page has a save URL, lets the researcher edit it in place.
   Text is always set through textContent, so whatever is typed is shown literally. */

const EMPTY_ABOUT = { title: "", description: "" };

function aboutHeading(about, noun) {
  return about.title || `Untitled ${noun}`;
}

async function saveAbout(url, draft) {
  const response = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: draft.title, description: draft.description }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = Array.isArray(payload.detail) ? payload.detail.map(item => item.msg).join("; ") : payload.detail;
    throw new Error(detail || `Saving failed (${response.status})`);
  }
  return response.json();
}

/* container: the element to fill; about: {title, description}; saveUrl: blank for a read-only page
   (a static export); noun: "game" or "tournament"; onSaved: called with the saved values. */
function mountAboutCard({ container, about, saveUrl, noun, onSaved }) {
  let current = { ...EMPTY_ABOUT, ...(about || {}) };
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  function showView() {
    container.replaceChildren();
    const empty = !current.title && !current.description;
    container.hidden = empty && !saveUrl;
    if (container.hidden) return;
    const head = make("div", "aboutHead");
    head.append(make("span", "eyebrow", `About this ${noun}`));
    if (saveUrl) {
      const edit = make("button", "btn aboutEdit", empty ? "+ add title & description" : "✎ edit");
      edit.type = "button";
      edit.onclick = showEditor;
      head.append(edit);
    }
    container.append(head);
    if (empty) {
      container.append(make("p", "aboutHint",
        `No title or description yet. Note what this ${noun} is for, e.g. where it is used in the report.`));
      return;
    }
    container.append(make("h2", "aboutTitle" + (current.title ? "" : " untitled"), aboutHeading(current, noun)));
    if (current.description) container.append(make("p", "aboutDescription", current.description));
  }

  function showEditor() {
    container.replaceChildren();
    const form = make("form", "aboutForm");
    const titleLabel = make("label", null, "Title");
    const titleInput = make("input");
    titleInput.maxLength = 200;
    titleInput.value = current.title;
    titleInput.placeholder = `A short name for this ${noun}`;
    titleLabel.append(titleInput);
    const descriptionLabel = make("label", null, "Description");
    const descriptionInput = make("textarea");
    descriptionInput.rows = 6;
    descriptionInput.maxLength = 20000;
    descriptionInput.value = current.description;
    descriptionInput.placeholder = "Setup, purpose, where it is reported, anything worth remembering";
    descriptionLabel.append(descriptionInput);
    const actions = make("div", "aboutActions");
    const save = make("button", "btn dark", "Save");
    save.type = "submit";
    const cancel = make("button", "btn", "Cancel");
    cancel.type = "button";
    cancel.onclick = showView;
    const error = make("p", "aboutError");
    error.setAttribute("role", "alert");
    actions.append(save, cancel);
    form.append(titleLabel, descriptionLabel, actions, error);
    form.onsubmit = async submitEvent => {
      submitEvent.preventDefault();
      save.disabled = true;
      error.textContent = "";
      try {
        current = await saveAbout(saveUrl, { title: titleInput.value, description: descriptionInput.value });
        if (onSaved) onSaved(current);
        showView();
      } catch (failure) {
        error.textContent = failure.message;
        save.disabled = false;
      }
    };
    container.append(form);
    titleInput.focus();
  }

  showView();
}
