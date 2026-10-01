import { formatPriceDate, priceAgeDays, STALE_AFTER_DAYS } from '../../lib/priceDate';

export function PriceDate({ value }: { value: string }) {
  const ageDays = priceAgeDays(value);
  const stale = ageDays > STALE_AFTER_DAYS;
  return (
    <span className={stale ? 'text-xs font-medium text-amber-700' : 'text-xs text-slate-500'}>
      Precio del {formatPriceDate(value)}
      {stale && <span> · Desactualizado ({ageDays} días)</span>}
    </span>
  );
}
