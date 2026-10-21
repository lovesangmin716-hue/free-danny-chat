// Native HTML modal semantics include focus containment and Escape handling.
let activeDialog;

export function closeThreadDialog() {
  activeDialog?.close();
}

export function openThreadDialog({ title, description, value, minLength = 1, maxLength = 2000, confirmLabel, onConfirm }) {
  closeThreadDialog();
  const dialog = document.createElement("dialog");
  dialog.className = "thread-dialog";
  dialog.setAttribute("aria-labelledby", "thread-dialog-title");
  dialog.setAttribute("aria-describedby", "thread-dialog-description");
  const form = document.createElement("form");
  const heading = document.createElement("h2"); heading.id = "thread-dialog-title"; heading.textContent = title;
  const explanation = document.createElement("p"); explanation.id = "thread-dialog-description"; explanation.textContent = description;
  const feedback = document.createElement("p"); feedback.setAttribute("role", "status"); feedback.setAttribute("aria-live", "polite");
  const controls = document.createElement("div"); controls.className = "thread-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.textContent = "취소";
  const confirm = document.createElement("button"); confirm.type = "submit"; confirm.textContent = confirmLabel;
  let input, counter, submitting = false;
  const valid = () => !input || (input.value.trim().length >= minLength && input.value.trim().length <= maxLength);
  const update = () => {
    confirm.disabled = submitting || !valid();
    if (counter) counter.textContent = `${input.value.length}/${maxLength}자`;
  };
  form.append(heading, explanation);
  if (value !== undefined) {
    input = document.createElement("textarea"); input.value = value; input.maxLength = maxLength;
    input.setAttribute("aria-label", title); input.required = true;
    input.addEventListener("input", update);
    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); if (!confirm.disabled) form.requestSubmit();
      }
    });
    counter = document.createElement("small");
    form.append(input, counter);
  }
  cancel.addEventListener("click", () => dialog.close());
  dialog.addEventListener("cancel", event => { if (submitting) event.preventDefault(); });
  dialog.addEventListener("close", () => {
    if (activeDialog === dialog) activeDialog = undefined;
    dialog.remove();
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (submitting || !valid()) return;
    submitting = true; cancel.disabled = true; if (input) input.disabled = true;
    feedback.textContent = "반영하는 중…"; update();
    try {
      await onConfirm(input?.value.trim());
      dialog.close();
    } catch (error) {
      feedback.textContent = error.message || "처리하지 못했습니다. 다시 시도해 주세요.";
    } finally {
      submitting = false; cancel.disabled = false; if (input) input.disabled = false;
      update(); if (dialog.open) input?.focus();
    }
  });
  controls.append(cancel, confirm); form.append(feedback, controls); dialog.append(form);
  document.body.append(dialog); activeDialog = dialog; update(); dialog.showModal();
  (input || cancel).focus();
}
