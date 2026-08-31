"use strict";

import { getDisplayName } from "./core.js";

export function actionButton(label, action, className = "secondary-button") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", action);
  return button;
}

export function emptyCopy(text) {
  const node = document.createElement("p");
  node.className = "ticket-empty";
  node.textContent = text;
  return node;
}

export function verifiedBadge(user) {
  if (!user?.ticket_verified) return null;
  const badge = document.createElement("span");
  badge.className = "ticket-verified";
  badge.textContent = "✓";
  badge.title = "관리자가 검증한 티켓 계정";
  badge.setAttribute("aria-label", "검증된 티켓 계정");
  return badge;
}

export function appendUserName(node, user, { handle = false } = {}) {
  node.append(document.createTextNode(`${getDisplayName(user || {})}${handle ? ` (@${user?.username || "-"})` : ""}`));
  const badge = verifiedBadge(user);
  if (badge) node.appendChild(badge);
}
