export function prevTradingDays(from, n) {
  const d = new Date(from);
  let remaining = n;
  while (remaining > 0) {
    d.setDate(d.getDate() - 1);
    if (d.getDay() !== 0 && d.getDay() !== 6) remaining--;
  }
  return d;
}
