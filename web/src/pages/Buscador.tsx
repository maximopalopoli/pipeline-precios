import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import {
  fetchBuscador,
  fetchBuscadorDates,
  fetchBuscadorGeneric,
  fetchBuscadorGenericDates,
  geocode,
  type Product,
  type StorePrice,
  type GenericStorePrice,
} from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { DataTable } from '../components/tables/DataTable';
import { PriceMap } from '../components/maps/PriceMap';
import { KpiCard } from '../components/layout/KpiCard';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { formatCurrency } from '../lib/constants';

// ── Single-product table columns ──
const col = createColumnHelper<StorePrice>();
const columns = [
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('tipo', { header: 'Tipo' }),
  col.accessor('localidad', { header: 'Localidad' }),
  col.accessor('provincia', { header: 'Provincia' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('mejor_precio', { header: 'Mejor precio', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('ahorro', { header: 'Ahorro', cell: (info) => formatCurrency(info.getValue()) }),
  col.display({
    id: 'promocion',
    header: 'Condiciones',
    cell: (info) => (
      <PromotionDetails
        tieneDescuento={info.row.original.tiene_descuento}
        leyendaPromo={info.row.original.leyenda_promo}
        compact
      />
    ),
  }),
];

// ── Helpers for generic mode ──
interface ChainGroup {
  cadena: string;
  count: number;
  min_precio: number;
  avg_precio: number;
  items: GenericStorePrice[];
}

function groupByChain(rows: GenericStorePrice[]): ChainGroup[] {
  const map = new Map<string, GenericStorePrice[]>();
  for (const row of rows) {
    const arr = map.get(row.cadena) || [];
    arr.push(row);
    map.set(row.cadena, arr);
  }
  return Array.from(map.entries())
    .map(([cadena, items]) => ({
      cadena,
      count: items.length,
      min_precio: Math.min(...items.map((i) => i.mejor_precio)),
      avg_precio: items.reduce((s, i) => s + i.mejor_precio, 0) / items.length,
      items: items.sort((a, b) => a.mejor_precio - b.mejor_precio),
    }))
    .sort((a, b) => a.min_precio - b.min_precio);
}

export function Buscador() {
  const { provincia, localidad, cadenas } = useFilters();

  // Two mutually exclusive modes
  const [product, setProduct] = useState<Product | null>(null);
  const [genericQuery, setGenericQuery] = useState<string | null>(null);

  const [fechaId, setFechaId] = useState<number | null>(null);
  const [showMap, setShowMap] = useState(false);
  const [expandedChains, setExpandedChains] = useState<Set<string>>(new Set());

  // ── Location filter ──
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [radius, setRadius] = useState(1);
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

  const handleSelectProduct = (p: Product | null) => {
    setProduct(p);
    setGenericQuery(null);
    setFechaId(null);
    setExpandedChains(new Set());
  };

  const handleSearchAll = (q: string) => {
    if (!q) {
      setGenericQuery(null);
      setFechaId(null);
      setExpandedChains(new Set());
      return;
    }
    setGenericQuery(q);
    setProduct(null);
    setFechaId(null);
    setExpandedChains(new Set());
  };

  const toggleChain = (cadena: string) => {
    setExpandedChains((prev) => {
      const next = new Set(prev);
      if (next.has(cadena)) next.delete(cadena);
      else next.add(cadena);
      return next;
    });
  };

  // ── Dates ──
  const { data: dates = [] } = useQuery({
    queryKey: ['buscador-dates', product?.producto_sk],
    queryFn: () => fetchBuscadorDates(product!.producto_sk),
    enabled: !!product,
  });

  const { data: genericDates = [] } = useQuery({
    queryKey: ['buscador-generic-dates', genericQuery],
    queryFn: () => fetchBuscadorGenericDates(genericQuery!),
    enabled: !!genericQuery,
  });

  const activeDates = genericQuery ? genericDates : dates;
  const selectedFechaId = fechaId ?? (activeDates.length > 0 ? activeDates[0].fecha_id : null);

  // ── Location params for API ──
  const locationParams = userLat != null && userLon != null
    ? { lat: userLat, lon: userLon, radius } : {};

  // ── Single-product results ──
  const { data: stores = [], isLoading: isLoadingSingle } = useQuery({
    queryKey: ['buscador', product?.producto_sk, selectedFechaId, provincia, localidad, cadenas, userLat, userLon, radius],
    queryFn: () =>
      fetchBuscador({
        producto_sk: product!.producto_sk,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
      }),
    enabled: !!product && !!selectedFechaId,
  });

  // ── Generic results ──
  const { data: genericRows = [], isLoading: isLoadingGeneric } = useQuery({
    queryKey: ['buscador-generic', genericQuery, selectedFechaId, provincia, localidad, cadenas, userLat, userLon, radius],
    queryFn: () =>
      fetchBuscadorGeneric({
        q: genericQuery!,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
      }),
    enabled: !!genericQuery && !!selectedFechaId,
  });

  const isLoading = isLoadingSingle || isLoadingGeneric;
  const isGenericMode = !!genericQuery;
  const chainGroups = isGenericMode ? groupByChain(genericRows) : [];
  const cheapest = !isGenericMode && stores.length > 0 ? stores[0] : null;
  const genericCheapest = isGenericMode && genericRows.length > 0
    ? genericRows.reduce((best, r) => (r.mejor_precio < best.mejor_precio ? r : best), genericRows[0])
    : null;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Buscador de Precios</h1>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        {/* Filters sidebar */}
        <div className="space-y-4">
          <FilterPanel showLocality />
          <ProductSearch
            onSelect={handleSelectProduct}
            onSearchAll={handleSearchAll}
            selected={product}
            genericQuery={genericQuery}
          />

          {activeDates.length > 0 && (
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Fecha</label>
              <select
                value={selectedFechaId ?? ''}
                onChange={(e) => setFechaId(Number(e.target.value))}
                className="w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm"
              >
                {activeDates.map((d) => (
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

        {/* Results */}
        <div className="space-y-4">
          {!product && !genericQuery && (
            <p className="text-sm text-slate-400">
              Busca un producto para ver precios, o presiona Enter para buscar todos los resultados.
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {/* Location active indicator */}
          {locationEnabled && userLat != null && (product || genericQuery) && !isLoading && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              Mostrando resultados a menos de {radius} km de tu ubicacion
              {((isGenericMode && genericRows.length === 0) || (!isGenericMode && stores.length === 0)) && selectedFechaId && (
                <span> — no hay locales cerca. Intenta aumentar el radio.</span>
              )}
            </div>
          )}

          {/* ── Single-product results ── */}
          {!isGenericMode && cheapest && (
            <>
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                <p className="text-sm text-emerald-800">
                  <strong>Mejor precio:</strong> {cheapest.cadena} — {cheapest.sucursal} ({cheapest.localidad})
                </p>
                  <p className="text-lg font-bold text-emerald-900">
                    {formatCurrency(cheapest.mejor_precio)}
                  {cheapest.ahorro > 0 && (
                    <span className="ml-2 text-sm font-normal">
                      (ahorro {formatCurrency(cheapest.ahorro)} vs lista)
                    </span>
                  )}
                </p>
                <PromotionDetails
                  tieneDescuento={cheapest.tiene_descuento}
                  leyendaPromo={cheapest.leyenda_promo}
                />
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Locales" value={String(stores.length)} />
                <KpiCard label="Precio min." value={formatCurrency(Math.min(...stores.map((s) => s.mejor_precio)))} />
                <KpiCard label="Precio max." value={formatCurrency(Math.max(...stores.map((s) => s.mejor_precio)))} />
                <KpiCard
                  label="Precio prom."
                  value={formatCurrency(stores.reduce((sum, s) => sum + s.mejor_precio, 0) / stores.length)}
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-lg font-semibold text-slate-800">Precios por local</h2>
                  <label className="flex items-center gap-2 text-sm text-slate-600">
                    <input
                      type="checkbox"
                      checked={showMap}
                      onChange={(e) => setShowMap(e.target.checked)}
                      className="rounded"
                    />
                    Ver mapa
                  </label>
                </div>
                {showMap && <PriceMap stores={stores} />}
                <DataTable data={stores} columns={columns} />
              </div>
            </>
          )}

          {/* ── Generic mode results ── */}
          {isGenericMode && genericRows.length > 0 && (
            <>
              {genericCheapest && (
                <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
                  <p className="text-sm text-emerald-800">
                    <strong>Mejor precio:</strong> {genericCheapest.cadena} — {genericCheapest.sucursal} ({genericCheapest.localidad})
                  </p>
                  <p className="text-lg font-bold text-emerald-900">
                    {formatCurrency(genericCheapest.mejor_precio)}
                  </p>
                  <p className="text-sm text-emerald-700">
                    {genericCheapest.producto} {genericCheapest.marca ? `— ${genericCheapest.marca}` : ''}
                  </p>
                </div>
              )}

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Productos" value={String(genericRows.length)} />
                <KpiCard label="Cadenas" value={String(chainGroups.length)} />
                <KpiCard label="Precio min." value={formatCurrency(Math.min(...genericRows.map((r) => r.mejor_precio)))} />
                <KpiCard
                  label="Precio prom."
                  value={formatCurrency(genericRows.reduce((s, r) => s + r.mejor_precio, 0) / genericRows.length)}
                />
              </div>

              {genericRows.length >= 1000 && (
                <p className="text-xs text-amber-600">
                  Mostrando los primeros 1000 resultados. Intenta una busqueda mas especifica.
                </p>
              )}

              <div className="space-y-2">
                <h2 className="text-lg font-semibold text-slate-800">Resultados por cadena</h2>
                {chainGroups.map((group) => (
                  <div key={group.cadena} className="rounded-lg border border-slate-200 overflow-hidden">
                    <button
                      onClick={() => toggleChain(group.cadena)}
                      className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
                    >
                      <div>
                        <span className="font-medium text-slate-900">{group.cadena}</span>
                        <span className="ml-2 text-sm text-slate-500">
                          ({group.count} {group.count === 1 ? 'producto' : 'productos'})
                        </span>
                      </div>
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-medium text-emerald-700">
                          desde {formatCurrency(group.min_precio)}
                        </span>
                        <span className="text-slate-400 text-xs">
                          {expandedChains.has(group.cadena) ? '▲' : '▼'}
                        </span>
                      </div>
                    </button>
                    {expandedChains.has(group.cadena) && (
                      <div className="divide-y divide-slate-100">
                        {group.items.map((item, i) => (
                          <div key={i} className="px-4 py-2.5 flex items-center justify-between text-sm">
                            <div>
                              <span className="text-slate-800">{item.producto}</span>
                              {item.marca && (
                                <span className="ml-1.5 text-slate-400">— {item.marca}</span>
                              )}
                              <span className="ml-2 text-xs text-slate-400">
                                {item.sucursal}, {item.localidad}
                              </span>
                            </div>
                            <div className="text-right whitespace-nowrap">
                              <span className="font-medium text-slate-900">
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
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {isGenericMode && !isLoading && genericRows.length === 0 && selectedFechaId && (
            <p className="text-sm text-slate-400">
              No se encontraron productos para "{genericQuery}".
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
