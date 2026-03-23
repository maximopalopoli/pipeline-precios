import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { fetchHealth, fetchKpis } from '../lib/api';
import { KpiCard } from '../components/layout/KpiCard';
import { COLORS } from '../lib/constants';

const STATUS_ICON: Record<string, string> = {
  completed: '🟢',
  running: '🟡',
  failed: '🔴',
};

const NAV_CARDS = [
  { to: '/buscador', icon: '🔍', label: 'Buscador de Precios', desc: 'Busca un producto y compara precios entre locales.' },
  { to: '/evolucion', icon: '📈', label: 'Evolucion de Precios', desc: 'Seguimiento del precio de un producto a lo largo del tiempo.' },
  { to: '/variaciones', icon: '🔀', label: 'Variaciones de Precios', desc: 'Productos con mayor cambio de precio en los ultimos 7 dias.' },
  { to: '/ofertas', icon: '🏷️', label: 'Ofertas del dia', desc: 'Los mejores descuentos y promociones vigentes hoy.' },
  { to: '/locales', icon: '📍', label: 'Locales Cercanos', desc: 'Encuentra supermercados cerca tuyo y ve sus ofertas.' },
];

export function Home() {
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: fetchHealth });
  const { data: kpis, isLoading } = useQuery({ queryKey: ['kpis'], queryFn: fetchKpis });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">SEPA Precios</h1>
        <p className="text-slate-500">
          Comparador de precios minoristas del sistema SEPA de transparencia de precios de Argentina.
        </p>
        {health && health.status !== 'no_data' && (
          <p className="mt-1 text-sm text-slate-400">
            {STATUS_ICON[health.status] || '⚪'} Datos al: {health.fecha_datos} — actualizado: {health.started_at?.slice(0, 16).replace('T', ' ')}
          </p>
        )}
      </div>

      {/* KPIs */}
      <div>
        <h2 className="text-lg font-semibold text-slate-800 mb-3">Resumen del dia</h2>
        {isLoading ? (
          <p className="text-sm text-slate-400">Cargando...</p>
        ) : kpis ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <KpiCard
              label="Variacion semanal prom."
              value={kpis.inflacion_semanal != null ? `${kpis.inflacion_semanal > 0 ? '+' : ''}${kpis.inflacion_semanal}%` : '—'}
              deltaColor={kpis.inflacion_semanal != null ? (kpis.inflacion_semanal > 0 ? 'red' : 'green') : 'neutral'}
            />
            <KpiCard label="Productos" value={kpis.total_productos.toLocaleString()} />
            <KpiCard label="Sucursales" value={kpis.total_sucursales.toLocaleString()} />
            <KpiCard
              label="Productos en promo"
              value={kpis.pct_promo != null ? `${kpis.pct_promo}%` : '—'}
            />
          </div>
        ) : null}
      </div>

      {/* Top variations */}
      {kpis && (
        <div className="grid md:grid-cols-2 gap-6">
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold text-slate-800 mb-3">Mayores subas</h3>
            {kpis.top_subas.length > 0 ? (
              <ul className="space-y-1.5">
                {kpis.top_subas.map((r, i) => (
                  <li key={i} className="text-sm">
                    <span className="font-semibold" style={{ color: COLORS.suba }}>
                      +{r.variacion.toFixed(1)}%
                    </span>{' '}
                    {r.producto.slice(0, 50)} ({r.provincia.slice(0, 10)})
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-400">Sin datos.</p>
            )}
          </div>
          <div className="rounded-lg border border-slate-200 bg-white p-4">
            <h3 className="font-semibold text-slate-800 mb-3">Mayores bajas</h3>
            {kpis.top_bajas.length > 0 ? (
              <ul className="space-y-1.5">
                {kpis.top_bajas.map((r, i) => (
                  <li key={i} className="text-sm">
                    <span className="font-semibold" style={{ color: COLORS.baja }}>
                      {r.variacion.toFixed(1)}%
                    </span>{' '}
                    {r.producto.slice(0, 50)} ({r.provincia.slice(0, 10)})
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-slate-400">Sin datos.</p>
            )}
          </div>
        </div>
      )}

      {/* Best promo chain */}
      {kpis?.best_chain && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
          <p className="text-sm text-emerald-800">
            <strong>Cadena con mas promos hoy:</strong> {kpis.best_chain.cadena}{' '}
            ({kpis.best_chain.pct_promo}% de sus productos en promocion)
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
