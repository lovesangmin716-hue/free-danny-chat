const TIME_CLUSTER_MS = 5 * 60 * 1000;

export function shouldShowMessageTime(message, nextMessage) {
  if (!nextMessage || nextMessage.username !== message.username) return true;
  const timestamp = Date.parse(message.timestamp);
  const nextTimestamp = Date.parse(nextMessage.timestamp);
  return !Number.isFinite(timestamp) || !Number.isFinite(nextTimestamp)
    || nextTimestamp < timestamp || nextTimestamp - timestamp > TIME_CLUSTER_MS;
}

export function messageReadReceiptLabel(message, room) {
  if (message.pending) return "보내는 중";
  if (message.failed) return "전송 실패";
  const count = Array.isArray(message.read_by) ? message.read_by.length : 0;
  return room?.kind === "group" ? `${count}명 읽음` : (count ? "읽음" : "안 읽음");
}
