export function PriceDate({ value }: { value: string }) {
  const [year, month, day] = value.split('-');
  return <span className="text-xs text-slate-500">Precio del {day}/{month}/{year}</span>;
}
