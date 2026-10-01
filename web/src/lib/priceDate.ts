export const STALE_AFTER_DAYS = 3;
const DAY_MS = 24 * 60 * 60 * 1000;

export function formatPriceDate(value: string): string {
  const [year, month, day] = value.split('-');
  return `${day}/${month}/${year}`;
}

function argentinaToday(): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Argentina/Buenos_Aires',
    year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((part) => part.type === type)?.value;
  return `${get('year')}-${get('month')}-${get('day')}`;
}

function utcDay(value: string): number {
  const [year, month, day] = value.split('-').map(Number);
  return Date.UTC(year, month - 1, day);
}

export function priceAgeDays(value: string, today = argentinaToday()): number {
  return Math.max(0, Math.floor((utcDay(today) - utcDay(value)) / DAY_MS));
}
