// Simple in-memory + sessionStorage store for the booking flow.
const KEY = "te_booking_state";

export function setFlow(state) {
  sessionStorage.setItem(KEY, JSON.stringify(state));
}
export function getFlow() {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}
export function clearFlow() {
  sessionStorage.removeItem(KEY);
}
