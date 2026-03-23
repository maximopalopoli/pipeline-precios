import { useQuery } from '@tanstack/react-query';
import { fetchProvinces, fetchChains, fetchLocalities } from '../../lib/api';
import { useFilters } from '../../hooks/useFilters';

interface FilterPanelProps {
  showLocality?: boolean;
}

export function FilterPanel({ showLocality = false }: FilterPanelProps) {
  const { provincia, localidad, cadenas, setProvince, setLocality, setChains } = useFilters();

  const { data: provinces = [] } = useQuery({
    queryKey: ['provinces'],
    queryFn: fetchProvinces,
  });

  const { data: chains = [] } = useQuery({
    queryKey: ['chains'],
    queryFn: fetchChains,
  });

  const { data: localities = [] } = useQuery({
    queryKey: ['localities', provincia],
    queryFn: () => fetchLocalities(provincia!),
    enabled: !!provincia && showLocality,
  });

  return (
    <div className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-700">Filtros</h3>

      {/* Province */}
      <div>
        <label className="block text-xs text-slate-500 mb-1">Provincia</label>
        <select
          value={provincia || ''}
          onChange={(e) => setProvince(e.target.value || null)}
          className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
        >
          <option value="">Todas</option>
          {provinces.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      {/* Locality */}
      {showLocality && provincia && localities.length > 0 && (
        <div>
          <label className="block text-xs text-slate-500 mb-1">Localidad</label>
          <select
            value={localidad || ''}
            onChange={(e) => setLocality(e.target.value || null)}
            className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
          >
            <option value="">Todas</option>
            {localities.map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
        </div>
      )}

      {/* Chains */}
      <div>
        <label className="block text-xs text-slate-500 mb-1">Cadenas</label>
        <select
          multiple
          value={cadenas}
          onChange={(e) =>
            setChains(Array.from(e.target.selectedOptions, (o) => o.value))
          }
          className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm min-h-[80px]"
        >
          {chains.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        {cadenas.length > 0 && (
          <button
            onClick={() => setChains([])}
            className="mt-1 text-xs text-blue-600 hover:underline"
          >
            Limpiar seleccion
          </button>
        )}
      </div>
    </div>
  );
}
