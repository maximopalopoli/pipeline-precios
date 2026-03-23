interface KpiCardProps {
  label: string;
  value: string;
  delta?: string;
  deltaColor?: 'green' | 'red' | 'neutral';
}

export function KpiCard({ label, value, delta, deltaColor = 'neutral' }: KpiCardProps) {
  const colorClass = {
    green: 'text-emerald-600',
    red: 'text-red-500',
    neutral: 'text-slate-500',
  }[deltaColor];

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-sm text-slate-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold text-slate-900">{value}</p>
      {delta && <p className={`mt-1 text-sm font-medium ${colorClass}`}>{delta}</p>}
    </div>
  );
}
