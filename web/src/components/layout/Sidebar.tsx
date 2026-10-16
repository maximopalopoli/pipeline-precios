import { NavLink, Outlet } from 'react-router-dom';
import { DataNotice } from './DataNotice';

const NAV_ITEMS = [
  { to: '/', label: 'Buscador', icon: '🔍' },
  { to: '/ofertas', label: 'Ofertas', icon: '🏷️' },
  { to: '/locales', label: 'Locales', icon: '📍' },
  { to: '/lista', label: 'Lista de Compras', icon: '🛒' },
];

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      {/* A sidebar from tablet width up; a top bar on phones */}
      <aside className="flex shrink-0 flex-col border-b border-slate-200 bg-white md:w-56 md:border-b-0 md:border-r">
        <div className="px-4 pt-3 md:border-b md:border-slate-200 md:p-4">
          <h1 className="text-lg font-bold text-slate-900">SEPA Precios</h1>
          <p className="hidden text-xs text-slate-500 md:block">Comparador de precios</p>
        </div>
        <nav className="flex gap-1 overflow-x-auto p-2 md:block md:space-y-1">
          {NAV_ITEMS.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex shrink-0 items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? 'bg-slate-100 text-slate-900 font-medium'
                    : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
                }`
              }
            >
              <span>{icon}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        {/* For whoever runs the ingestion, not part of shopping. */}
        <NavLink
          to="/estado"
          className={({ isActive }) =>
            `mt-auto hidden border-t border-slate-200 px-5 py-3 text-xs md:block ${
              isActive ? 'text-slate-900 font-medium' : 'text-slate-400 hover:text-slate-600'
            }`
          }
        >
          Estado de los datos
        </NavLink>
      </aside>

      {/* Main content */}
      <main className="min-w-0 flex-1 overflow-auto bg-slate-50 p-4 md:p-6">
        <DataNotice />
        <Outlet />
      </main>
    </div>
  );
}
