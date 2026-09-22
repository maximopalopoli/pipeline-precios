import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  fetchBuscadorEan,
  fetchBuscadorEanDates,
  geocode,
  type EanStorePrice,
} from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { KpiCard } from '../components/layout/KpiCard';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { formatCurrency } from '../lib/constants';

// ── Group results by EAN product ──

interface ProductGroup {
  producto_sk: number;
  id_producto: string;
  producto: string;
  marca: string | null;
  cantidad: string | null;
  unidad: string | null;
  cadenas_count: number;
  min_precio: number;
  max_precio: number;
  items: EanStorePrice[];
}

function groupByProduct(rows: EanStorePrice[]): ProductGroup[] {
  const map = new Map<number, EanStorePrice[]>();
  for (const row of rows) {
    const arr = map.get(row.producto_sk) || [];
    arr.push(row);
    map.set(row.producto_sk, arr);
  }
  return Array.from(map.entries())
    .map(([producto_sk, items]) => {
      const first = items[0];
      const cadenas = new Set(items.map((i) => i.cadena));
      return {
        producto_sk,
        id_producto: first.id_producto,
        producto: first.producto,
        marca: first.marca,
        cantidad: first.cantidad,
        unidad: first.unidad,
        cadenas_count: cadenas.size,
        min_precio: Math.min(...items.map((i) => i.mejor_precio)),
        max_precio: Math.max(...items.map((i) => i.mejor_precio)),
        items: items.sort((a, b) => a.mejor_precio - b.mejor_precio),
      };
    })
    .sort((a, b) => a.min_precio - b.min_precio);
}

export function BuscadorEAN() {
  const { provincia, localidad, cadenas } = useFilters();

  const [query, setQuery] = useState('');
  const [searchTerm, setSearchTerm] = useState<string | null>(null);
  const [fechaId, setFechaId] = useState<number | null>(null);
  const [expandedProducts, setExpandedProducts] = useState<Set<number>>(new Set());

  // ── Location ──
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [radius, setRadius] = useState(3);
  const [geoTrigger, setGeoTrigger] = useState(0);
  const [locationEnabled, setLocationEnabled] = useState(false);

  const { data: geoResult, isFetching: isGeocoding } = useQuery({
    queryKey: ['geocode', address, city, geoTrigger],
    queryFn: () => geocode(address, city),
    enabled: address.length > 3 && geoTrigger > 0,
  });

  const userLat = locationEnabled && geoResult?.lat ? geoResult.lat : null;
  const userLon = locationEnabled && geoResult?.lon ? geoResult.lon : null;

  const handleGeoSearch = () => {
    setLocationEnabled(true);
    setGeoTrigger((n) => n + 1);
  };

  const clearLocation = () => {
    setLocationEnabled(false);
    setAddress('');
    setGeoTrigger(0);
  };

  const handleSearch = () => {
    const q = query.trim();
    if (q.length >= 2) {
      setSearchTerm(q);
      setFechaId(null);
      setExpandedProducts(new Set());
    }
  };

  const toggleProduct = (sk: number) => {
    setExpandedProducts((prev) => {
      const next = new Set(prev);
      if (next.has(sk)) next.delete(sk);
      else next.add(sk);
      return next;
    });
  };

  // ── Dates ──
  const { data: dates = [] } = useQuery({
    queryKey: ['ean-dates', searchTerm],
    queryFn: () => fetchBuscadorEanDates(searchTerm!),
    enabled: !!searchTerm,
  });

  const selectedFechaId = fechaId ?? (dates.length > 0 ? dates[0].fecha_id : null);

  // ── Location params ──
  const locationParams = userLat != null && userLon != null
    ? { lat: userLat, lon: userLon, radius } : {};

  // ── Results ──
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['ean-search', searchTerm, selectedFechaId, provincia, localidad, cadenas, userLat, userLon, radius],
    queryFn: () =>
      fetchBuscadorEan({
        q: searchTerm!,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
      }),
    enabled: !!searchTerm && !!selectedFechaId,
  });

  const productGroups = groupByProduct(rows);
  const totalCadenas = new Set(rows.map((r) => r.cadena)).size;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Comparador EAN</h1>
        <p className="text-sm text-slate-500 mt-1">
          Compara el mismo producto (codigo de barras) entre distintas cadenas
        </p>
      </div>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        {/* Sidebar */}
        <div className="space-y-4">
          <FilterPanel showLocality />

          {dates.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Fecha</label>
              <select
                value={selectedFechaId ?? ''}
                onChange={(e) => setFechaId(Number(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {dates.map((d) => (
                  <option key={d.fecha_id} value={d.fecha_id}>
                    {d.fecha}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Location filter */}
          <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-700">Filtrar por ubicacion</h3>
              {locationEnabled && (
                <button onClick={clearLocation} className="text-xs text-slate-400 hover:text-slate-600">
                  Desactivar
                </button>
              )}
            </div>
            <input
              type="text"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && address.length > 3) handleGeoSearch(); }}
              placeholder="Av. Corrientes 1234"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
            <input
              type="text"
              value={city}
              onChange={(e) => setCity(e.target.value)}
              placeholder="Ciudad"
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
            />
            <button
              onClick={handleGeoSearch}
              disabled={address.length < 3}
              className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {isGeocoding ? 'Buscando...' : 'Buscar ubicacion'}
            </button>
            {locationEnabled && geoResult?.lat && (
              <>
                <p className="text-xs text-slate-500">
                  Ubicacion: {geoResult.lat.toFixed(4)}, {geoResult.lon!.toFixed(4)}
                </p>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Radio: {radius} km</label>
                  <input
                    type="range"
                    min={0.5}
                    max={10}
                    step={0.5}
                    value={radius}
                    onChange={(e) => setRadius(Number(e.target.value))}
                    className="w-full"
                  />
                </div>
              </>
            )}
            {locationEnabled && geoResult && !geoResult.lat && (
              <p className="text-xs text-red-500">No se encontro la direccion.</p>
            )}
          </div>
        </div>

        {/* Main area */}
        <div className="space-y-4">
          {/* Search bar */}
          <div className="flex gap-2">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
              placeholder="Buscar producto EAN... (ej: leche, coca cola, arroz)"
              className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
            <button
              onClick={handleSearch}
              disabled={query.trim().length < 2}
              className="rounded-md bg-blue-600 px-5 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Buscar
            </button>
          </div>

          {!searchTerm && (
            <p className="text-sm text-slate-400">
              Busca un producto para comparar precios del mismo codigo de barras entre cadenas.
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {/* Location indicator */}
          {locationEnabled && userLat != null && searchTerm && !isLoading && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              Mostrando resultados a menos de {radius} km de tu ubicacion
              {rows.length === 0 && selectedFechaId && (
                <span> — no hay locales cerca. Intenta aumentar el radio.</span>
              )}
            </div>
          )}

          {/* KPIs */}
          {rows.length > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <KpiCard label="Productos EAN" value={String(productGroups.length)} />
              <KpiCard label="Cadenas" value={String(totalCadenas)} />
              <KpiCard label="Precio min." value={formatCurrency(Math.min(...rows.map((r) => r.mejor_precio)))} />
              <KpiCard
                label="Resultados"
                value={String(rows.length)}
              />
            </div>
          )}

          {rows.length >= 1000 && (
            <p className="text-xs text-amber-600">
              Mostrando los primeros 1000 resultados. Intenta una busqueda mas especifica.
            </p>
          )}

          {/* Product groups */}
          {productGroups.length > 0 && (
            <div className="space-y-2">
              {productGroups.map((group) => (
                <div key={group.producto_sk} className="rounded-lg border border-slate-200 overflow-hidden">
                  <button
                    onClick={() => toggleProduct(group.producto_sk)}
                    className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        {group.marca && (
                          <span className="font-semibold text-slate-900">{group.marca}</span>
                        )}
                        {group.cantidad && group.unidad && (
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                            {group.cantidad} {group.unidad}
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-500 truncate mt-0.5">{group.producto}</p>
                      <div className="flex items-center gap-3 mt-0.5">
                        <span className="text-xs text-slate-400 font-mono">{group.id_producto}</span>
                        <span className="text-xs text-slate-500">
                          {group.cadenas_count} {group.cadenas_count === 1 ? 'cadena' : 'cadenas'} · {group.items.length} {group.items.length === 1 ? 'local' : 'locales'}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-3 shrink-0 ml-4">
                      <div className="text-right">
                        <span className="text-sm font-medium text-emerald-700">
                          {formatCurrency(group.min_precio)}
                        </span>
                        {group.min_precio !== group.max_precio && (
                          <span className="text-xs text-slate-400 ml-1">
                            — {formatCurrency(group.max_precio)}
                          </span>
                        )}
                      </div>
                      <span className="text-slate-400 text-xs">
                        {expandedProducts.has(group.producto_sk) ? '\u25B2' : '\u25BC'}
                      </span>
                    </div>
                  </button>
                  {expandedProducts.has(group.producto_sk) && (
                    <div className="divide-y divide-slate-100">
                      {group.items.map((item, i) => {
                        const isCheapest = item.mejor_precio === group.min_precio;
                        return (
                          <div
                            key={i}
                            className={`px-4 py-2.5 flex items-center justify-between text-sm ${
                              isCheapest ? 'bg-emerald-50' : ''
                            }`}
                          >
                            <div className="flex-1 min-w-0">
                              <span className="font-medium text-slate-800">{item.cadena}</span>
                              <span className="ml-2 text-sm text-slate-500">{item.sucursal}</span>
                              <div className="text-xs text-slate-400">
                                {item.calle && (
                                  <span>{item.calle}{item.numero ? ` ${item.numero}` : ''} · </span>
                                )}
                                {item.localidad}, {item.provincia}
                              </div>
                            </div>
                            <div className="flex items-center gap-2 shrink-0 ml-4">
                              <div className="text-right whitespace-nowrap">
                                <span className={`font-medium ${isCheapest ? 'text-emerald-700' : 'text-slate-900'}`}>
                                  {formatCurrency(item.mejor_precio)}
                                </span>
                                {item.precio_lista !== item.mejor_precio && (
                                  <span className="ml-2 text-xs text-slate-400 line-through">
                                    {formatCurrency(item.precio_lista)}
                                  </span>
                                )}
                                <div>
                                  <PromotionDetails
                                    tieneDescuento={item.tiene_descuento}
                                    leyendaPromo={item.leyenda_promo}
                                    compact
                                  />
                                </div>
                              </div>
                              {isCheapest && (
                                <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                                  Mejor
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {searchTerm && !isLoading && rows.length === 0 && selectedFechaId && (
            <p className="text-sm text-slate-400">
              No se encontraron productos EAN para "{searchTerm}".
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
