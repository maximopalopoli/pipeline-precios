import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { fetchHealth } from '../lib/api';
import { KpiCard } from '../components/layout/KpiCard';
import { QueryError } from '../components/layout/QueryError';
import { formatPriceDate } from '../lib/priceDate';

const NAV_CARDS = [
  { to: '/buscador', icon: '🔍', label: 'Buscador de Precios', desc: 'Busca un producto y compara precios entre locales.' },
  { to: '/ofertas', icon: '🏷️', label: 'Ofertas del dia', desc: 'Los mejores descuentos y promociones vigentes hoy.' },
  { to: '/locales', icon: '📍', label: 'Locales Cercanos', desc: 'Encuentra supermercados cerca tuyo y ve sus ofertas.' },
];

function formatCount(value: number | null) {
  return value == null ? '—' : value.toLocaleString();
}

export function Home() {
  // Missing, stale and failed publications are announced by the layout's DataNotice.
  const { data: health, isError, error, refetch } = useQuery({
    queryKey: ['health'], queryFn: fetchHealth, refetchInterval: 60_000,
  });
  const reconciliation = health?.reconciliacion;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">SEPA Precios</h1>
        <p className="text-slate-500">
          Comparador de precios minoristas del sistema SEPA de transparencia de precios de Argentina.
        </p>
        {health?.ultima_publicacion ? (
          <p className="mt-1 text-sm text-slate-600">
            Últimos precios publicados: {formatPriceDate(health.ultima_publicacion.fecha_datos)}
            {' · '}Ingesta completada: {new Date(health.ultima_publicacion.finished_at).toLocaleString('es-AR', { timeZone: 'America/Argentina/Buenos_Aires' })}
          </p>
        ) : null}
        {isError && (
          <div className="mt-2">
            <QueryError what="el estado de los datos" error={error} onRetry={() => refetch()} />
          </div>
        )}
      </div>

      {reconciliation && (
        <div>
          <h2 className="text-lg font-semibold text-slate-800 mb-3">Calidad de la última ingesta</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <KpiCard label="Filas recibidas" value={formatCount(reconciliation.rows_productos_raw)} />
            <KpiCard label="Filas válidas" value={formatCount(reconciliation.rows_productos_valid)} />
            <KpiCard label="Filas rechazadas" value={formatCount(reconciliation.rows_productos_rejected)} />
            <KpiCard label="Precios publicados" value={formatCount(reconciliation.rows_fact_published)} />
          </div>
          <p className="mt-2 text-sm text-slate-500">
            Sin claves: {formatCount(reconciliation.rows_productos_without_keys)} · Duplicadas:{' '}
            {formatCount(reconciliation.rows_productos_deduplicated)} · Pendientes de publicar:{' '}
            {formatCount(reconciliation.rows_fact_pending)}
          </p>
        </div>
      )}

      {/* Navigation cards */}
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-3">Explorar</h2>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {NAV_CARDS.map(({ to, icon, label, desc }) => (
            <Link
              key={to}
              to={to}
              className="rounded-lg border border-slate-200 bg-white p-4 transition-shadow hover:shadow-md"
            >
              <p className="text-lg">
                {icon} <span className="font-semibold text-slate-900">{label}</span>
              </p>
              <p className="mt-1 text-sm text-slate-500">{desc}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
