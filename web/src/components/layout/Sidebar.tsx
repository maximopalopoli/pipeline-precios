import { NavLink, Outlet } from 'react-router-dom';
import { DataNotice } from './DataNotice';

const NAV_ITEMS = [
  { to: '/', label: 'Inicio', icon: '📊' },
  { to: '/buscador', label: 'Buscador', icon: '🔍' },
  { to: '/ofertas', label: 'Ofertas', icon: '🏷️' },
  { to: '/locales', label: 'Locales', icon: '📍' },
  { to: '/lista', label: 'Lista de Compras', icon: '🛒' },
];

export function AppLayout() {
  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="w-56 shrink-0 border-r border-slate-200 bg-white">
        <div className="p-4 border-b border-slate-200">
          <h1 className="text-lg font-bold text-slate-900">SEPA Precios</h1>
          <p className="text-xs text-slate-500">Comparador de precios</p>
        </div>
        <nav className="p-2 space-y-1">
          {NAV_ITEMS.map(({ to, label, icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors ${
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
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-slate-50 p-6">
        <DataNotice />
        <Outlet />
      </main>
    </div>
  );
}
