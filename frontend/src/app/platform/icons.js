"use strict";

// Dependency-free SVG icon rendering for static and dynamically-created controls.
const namespace = "http://www.w3.org/2000/svg";
const icons = Object.freeze({
    "arrow-left": '<path d="m15 18-6-6 6-6"/><path d="M21 12H9"/>',
    "log-out": '<path d="M10 17l5-5-5-5"/><path d="M15 12H3"/><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/>',
    "message-circle": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3 1.7-5.1A8 8 0 1 1 21 15Z"/>',
    "message-plus": '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3 1.7-5.1A8 8 0 1 1 21 15Z"/><path d="M12 8v6M9 11h6"/>',
    "mic": '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>',
    "paperclip": '<path d="m20.5 11.5-8.9 8.9a6 6 0 0 1-8.5-8.5l9.6-9.6a4 4 0 0 1 5.7 5.7l-9.6 9.6a2 2 0 0 1-2.8-2.8l8.9-8.9"/>',
    "play": '<path d="m8 5 11 7-11 7Z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
    "send": '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/>',
    "square": '<rect x="6" y="6" width="12" height="12" rx="1"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "user-plus": '<path d="M15 21a7 7 0 0 0-14 0"/><circle cx="8" cy="7" r="4"/><path d="M19 8v6M16 11h6"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="8.5" cy="7" r="4"/><path d="M20 8v6M17 11h6"/>',
    "volume-2": '<path d="M11 5 6 9H2v6h4l5 4Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18 6a8.5 8.5 0 0 1 0 12"/>',
    "volume-x": '<path d="M11 5 6 9H2v6h4l5 4Z"/><path d="m22 9-6 6M16 9l6 6"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
  });

export function createIcon(name) {
    if (!icons[name]) throw new Error(`Unknown icon: ${name}`);
    const svg = document.createElementNS(namespace, "svg");
    svg.classList.add("ui-icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.innerHTML = icons[name];
    return svg;
  }

export function decorateIconButton(button, name = button?.dataset.icon, options = {}) {
    if (!button || !name) return button;
    const label = options.label || button.dataset.iconLabel || button.getAttribute("aria-label") || button.textContent.trim();
    const iconOnly = options.iconOnly ?? button.hasAttribute("data-icon-only");
    const visibleLabel = options.visibleLabel ?? !iconOnly;
    button.dataset.icon = name;
    button.classList.toggle("icon-button", iconOnly);
    button.classList.toggle("icon-label-button", visibleLabel);
    if (label) button.setAttribute("aria-label", label);

    const content = [createIcon(name)];
    if (label) {
      const text = document.createElement("span");
      text.className = visibleLabel ? "button-label" : "sr-only";
      text.textContent = label;
      content.push(text);
    }
    button.replaceChildren(...content);
    return button;
  }

export function hydrateIcons(root = document) {
    root.querySelectorAll("[data-icon]").forEach((button) => decorateIconButton(button));
  }

hydrateIcons();
