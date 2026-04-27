// Pretty price formatter. For SGD-priced trips we append the bracketed MYR approx
// using a rate fetched from /api/settings (default 3.50 if call hasn't returned yet).
const SYM = { myr: "RM", sgd: "SGD", usd: "USD" };

export function formatPrice(amount, currency) {
  const c = (currency || "myr").toLowerCase();
  const sym = SYM[c] || c.toUpperCase();
  const a = Number(amount || 0).toFixed(2);
  // RM is glued (RM 35), other ISO codes are spaced (SGD 25)
  return `${sym} ${a}`;
}

export function formatPriceWithMyr(amount, currency, sgdToMyrRate) {
  const c = (currency || "myr").toLowerCase();
  const primary = formatPrice(amount, currency);
  if (c !== "sgd" || !sgdToMyrRate) return primary;
  const myr = (Number(amount) * Number(sgdToMyrRate)).toFixed(2);
  return `${primary} (≈ RM ${myr})`;
}
