import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  fetchBuscadorGeneric,
  fetchBuscadorGenericDates,
  geocode,
  type GenericStorePrice,
} from '../lib/api';
import { useShoppingList } from '../hooks/useShoppingList';
import { useSearchRadius } from '../hooks/useSearchRadius';
import { RadiusNotice } from '../components/filters/RadiusNotice';
import { formatCurrency } from '../lib/constants';
import { PriceDate } from '../components/pricing/PriceDate';

// ── Step 0: Build shopping list ──

function BuildList() {
  const { items, addItem, removeItem, clearItems, lat, lon, radius, setLocation, setRadius, clearLocation, setStep } =
    useShoppingList();
  const [input, setInput] = useState('');

  // ── Browser geolocation ──
  const [geoLoading, setGeoLoading] = useState(false);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [showAddressForm, setShowAddressForm] = useState(false);

  const handleUseMyLocation = () => {
    if (!navigator.geolocation) {
      setGeoError('Tu navegador no soporta geolocalizacion.');
      setShowAddressForm(true);
      return;
    }
    setGeoLoading(true);
    setGeoError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation(pos.coords.latitude, pos.coords.longitude);
        setGeoLoading(false);
      },
      (err) => {
        const msg = err.code === 1
          ? 'Permiso denegado. Usa la busqueda por direccion.'
          : 'No se pudo obtener la ubicacion. Usa la busqueda por direccion.';
        setGeoError(msg);
        setShowAddressForm(true);
        setGeoLoading(false);
      },
      { enableHighAccuracy: false, timeout: 10000 },
    );
  };

  // ── Geocoding fallback ──
  const [address, setAddress] = useState('');
  const [city, setCity] = useState('Buenos Aires');
  const [geoTrigger, setGeoTrigger] = useState(0);

  const { data: geoResult, isFetching: isGeocoding } = useQuery({
    queryKey: ['geocode', address, city, geoTrigger],
    queryFn: () => geocode(address, city),
    enabled: address.length > 3 && geoTrigger > 0,
  });

  // Sync geocode result to store
  const geoLat = geoResult?.lat;
  const geoLon = geoResult?.lon;
  if (geoLat && geoLon && (geoLat !== lat || geoLon !== lon)) {
    setLocation(geoLat, geoLon);
  }

  const handleGeoSearch = () => setGeoTrigger((n) => n + 1);

  const handleAdd = () => {
    if (input.trim().length >= 2) {
      addItem(input);
      setInput('');
    }
  };

  return (
    <div className="grid md:grid-cols-[280px_1fr] gap-6">
      {/* Sidebar */}
      <div className="space-y-4">
        {/* Location */}
        <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-700">Ubicacion</h3>
            {lat != null && (
              <button onClick={() => { clearLocation(); setGeoError(null); }} className="text-xs text-slate-400 hover:text-slate-600">
                Limpiar
              </button>
            )}
          </div>

          {lat == null && (
            <>
              <button
                onClick={handleUseMyLocation}
                disabled={geoLoading}
                className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {geoLoading ? 'Obteniendo ubicacion...' : 'Usar mi ubicacion'}
              </button>

              {geoError && (
                <p className="text-xs text-red-500">{geoError}</p>
              )}

              {!showAddressForm && (
                <button
                  onClick={() => setShowAddressForm(true)}
                  className="w-full text-xs text-slate-400 hover:text-slate-600"
                >
                  O buscar por direccion
                </button>
              )}

              {showAddressForm && (
                <>
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
                    className="w-full rounded-md bg-slate-600 px-3 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
                  >
                    {isGeocoding ? 'Buscando...' : 'Buscar direccion'}
                  </button>
                  {geoResult && !geoResult.lat && (
                    <p className="text-xs text-red-500">No se encontro la direccion.</p>
                  )}
                </>
              )}
            </>
          )}

          {lat != null && lon != null && (
            <>
              <p className="text-xs text-emerald-600 font-medium">Ubicacion configurada</p>
              <p className="text-xs text-slate-500">
                {lat.toFixed(4)}, {lon.toFixed(4)}
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
        </div>
      </div>

      {/* Main area */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold text-slate-800">Armar lista de compras</h2>

        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(); }}
            placeholder="Ej: leche, pan, arroz..."
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={handleAdd}
            disabled={input.trim().length < 2}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Agregar
          </button>
        </div>

        {items.length === 0 && (
          <p className="text-sm text-slate-400">Agrega productos a tu lista para empezar.</p>
        )}

        {items.length > 0 && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {items.map((item, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-3 py-1.5 text-sm text-blue-800"
                >
                  {item}
                  <button
                    onClick={() => removeItem(i)}
                    className="text-blue-400 hover:text-blue-600"
                  >
                    ✕
                  </button>
                </span>
              ))}
            </div>

            <div className="flex items-center gap-3">
              <button
                onClick={() => setStep(1)}
                className="rounded-md bg-emerald-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-emerald-700"
              >
                Comenzar ({items.length} {items.length === 1 ? 'producto' : 'productos'})
              </button>
              <button
                onClick={clearItems}
                className="text-sm text-slate-400 hover:text-slate-600"
              >
                Limpiar lista
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Step 1..N: Select product for each item ──

function SelectProduct({ itemIndex }: { itemIndex: number }) {
  const { items, selections, setSelection, setStep, nextStep, prevStep, lat, lon, radius } =
    useShoppingList();

  const searchTerm = items[itemIndex];
  const currentSelection = selections[itemIndex] ?? null;
  const [expandedChains, setExpandedChains] = useState<Set<string>>(new Set());

  const totalItems = items.length;
  const completedCount = Object.keys(selections).length;

  // Location params
  const searchArea = useSearchRadius(lat, lon, radius);
  const searchRadius = searchArea.radius;
  const locationParams = lat != null && lon != null ? { lat, lon, radius: searchRadius } : {};

  // Get available dates
  const { data: dates = [] } = useQuery({
    queryKey: ['lista-dates', searchTerm],
    queryFn: () => fetchBuscadorGenericDates(searchTerm),
    enabled: !!searchTerm,
  });

  const fechaId = dates.length > 0 ? dates[0].fecha_id : null;

  // Search results
  const { data: results = [], isLoading } = useQuery({
    queryKey: ['lista-search', searchTerm, fechaId, lat, lon, searchRadius],
    queryFn: () =>
      fetchBuscadorGeneric({
        q: searchTerm,
        fecha_id: fechaId!,
        ...locationParams,
      }),
    enabled: !!searchTerm && !!fechaId && searchArea.ready,
  });

  // Group results by chain
  const chainGroups = groupByChain(results);

  const toggleChain = (cadena: string) => {
    setExpandedChains((prev) => {
      const next = new Set(prev);
      if (next.has(cadena)) next.delete(cadena);
      else next.add(cadena);
      return next;
    });
  };

  const handleSelect = (product: GenericStorePrice) => {
    setSelection(itemIndex, product);
  };

  const allSelected = completedCount === totalItems;

  return (
    <div className="space-y-4">
      {/* Progress bar */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-slate-600">
            Producto {itemIndex + 1} de {totalItems}
          </span>
          <span className="text-slate-400">
            {completedCount}/{totalItems} seleccionados
          </span>
        </div>
        <div className="h-2 rounded-full bg-slate-100">
          <div
            className="h-2 rounded-full bg-emerald-500 transition-all"
            style={{ width: `${(completedCount / totalItems) * 100}%` }}
          />
        </div>
      </div>

      {/* Item navigation */}
      <div className="flex items-center gap-2 flex-wrap">
        {items.map((item, i) => (
          <button
            key={i}
            onClick={() => setStep(i + 1)}
            className={`rounded-full px-3 py-1 text-sm transition-colors ${
              i === itemIndex
                ? 'bg-blue-600 text-white'
                : selections[i]
                  ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                  : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {selections[i] ? '✓ ' : ''}{item}
          </button>
        ))}
      </div>

      {/* Current item header */}
      <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
        <h2 className="text-lg font-semibold text-blue-900">
          Buscar: "{searchTerm}"
        </h2>
        {currentSelection && (
          <p className="text-sm text-blue-700 mt-1">
            Seleccionado: {currentSelection.producto}
            {currentSelection.marca ? ` — ${currentSelection.marca}` : ''}{' '}
            en {currentSelection.cadena} ({currentSelection.sucursal}) —{' '}
            <span className="font-medium">{formatCurrency(currentSelection.mejor_precio)}</span>
            {' '}<PriceDate value={currentSelection.fecha_precio} />
            <button
              onClick={() => useShoppingList.getState().clearSelection(itemIndex)}
              className="ml-2 text-blue-500 hover:text-blue-700 underline"
            >
              Cambiar
            </button>
          </p>
        )}
      </div>

      {/* Location indicator */}
      {lat != null && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-4 py-2 text-sm text-blue-700">
          Mostrando resultados a menos de {searchRadius} km de tu ubicacion
          <RadiusNotice area={searchArea.area} />
        </div>
      )}

      {/* Results */}
      {isLoading && <p className="text-sm text-slate-400">Cargando resultados...</p>}

      {!isLoading && results.length === 0 && fechaId && (
        <p className="text-sm text-slate-400">No se encontraron productos para "{searchTerm}".</p>
      )}

      {results.length > 0 && (
        <div className="space-y-2">
          {chainGroups.map((group) => (
            <div key={group.cadena} className="rounded-lg border border-slate-200 overflow-hidden">
              <button
                onClick={() => toggleChain(group.cadena)}
                className="w-full flex items-center justify-between px-4 py-3 bg-slate-50 hover:bg-slate-100 transition-colors text-left"
              >
                <div>
                  <span className="font-medium text-slate-900">{group.cadena}</span>
                  <span className="ml-2 text-sm text-slate-500">
                    ({group.count} {group.count === 1 ? 'resultado' : 'resultados'})
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
                  {group.items.map((item, i) => {
                    const isSelected =
                      currentSelection?.producto === item.producto &&
                      currentSelection?.sucursal === item.sucursal &&
                      currentSelection?.cadena === item.cadena;
                    return (
                      <div
                        key={i}
                        className={`px-4 py-2.5 flex items-center justify-between text-sm ${
                          isSelected ? 'bg-emerald-50' : ''
                        }`}
                      >
                        <div className="flex-1">
                          <span className="text-slate-800">{item.producto}</span>
                          {item.marca && (
                            <span className="ml-1.5 text-slate-400">— {item.marca}</span>
                          )}
                          <span className="ml-2 text-xs text-slate-400">
                            {item.sucursal}, {item.localidad}
                          </span>
                        </div>
                        <div className="flex items-center gap-3">
                          <div className="text-right whitespace-nowrap">
                            <span className="font-medium text-slate-900">
                              {formatCurrency(item.mejor_precio)}
                            </span>
                            {item.precio_lista !== item.mejor_precio && (
                              <span className="ml-2 text-xs text-slate-400 line-through">
                                {formatCurrency(item.precio_lista)}
                              </span>
                            )}
                            {item.precio_condicional != null && (
                              <div
                                className="text-xs text-amber-700"
                                title={item.leyenda_condicional ?? undefined}
                              >
                                {formatCurrency(item.precio_condicional)} con condiciones
                              </div>
                            )}
                            <div><PriceDate value={item.fecha_precio} /></div>
                          </div>
                          <button
                            onClick={() => handleSelect(item)}
                            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                              isSelected
                                ? 'bg-emerald-600 text-white'
                                : 'bg-slate-100 text-slate-700 hover:bg-blue-100 hover:text-blue-700'
                            }`}
                          >
                            {isSelected ? '✓ Elegido' : 'Elegir'}
                          </button>
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

      {/* Navigation */}
      <div className="flex items-center justify-between pt-4 border-t border-slate-200">
        <button
          onClick={prevStep}
          className="rounded-md border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          {itemIndex === 0 ? 'Volver a la lista' : 'Anterior'}
        </button>
        <div className="flex gap-2">
          {itemIndex < totalItems - 1 && (
            <button
              onClick={nextStep}
              className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700"
            >
              Siguiente
            </button>
          )}
          {allSelected && (
            <button
              onClick={() => setStep(totalItems + 1)}
              className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
            >
              Ver resumen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Summary: products grouped by store ──

function Summary() {
  const { items, selections, setStep, reset } = useShoppingList();

  // Group selections by store key (cadena + sucursal + localidad)
  const storeMap = new Map<string, { cadena: string; sucursal: string; localidad: string; provincia: string; items: { searchTerm: string; product: GenericStorePrice }[] }>();

  for (const [indexStr, product] of Object.entries(selections)) {
    const index = Number(indexStr);
    const key = `${product.cadena}|${product.sucursal}|${product.localidad}`;
    if (!storeMap.has(key)) {
      storeMap.set(key, {
        cadena: product.cadena,
        sucursal: product.sucursal,
        localidad: product.localidad,
        provincia: product.provincia,
        items: [],
      });
    }
    storeMap.get(key)!.items.push({ searchTerm: items[index], product });
  }

  const stores = Array.from(storeMap.values()).sort((a, b) => a.cadena.localeCompare(b.cadena));
  const grandTotal = Object.values(selections).reduce((sum, s) => sum + s.mejor_precio, 0);
  const selectedCount = Object.keys(selections).length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Resumen de compras</h2>
        <div className="flex gap-2">
          <button
            onClick={() => setStep(1)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-50"
          >
            Editar selecciones
          </button>
          <button
            onClick={reset}
            className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50"
          >
            Nueva lista
          </button>
        </div>
      </div>

      {/* Grand total */}
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
        <p className="text-sm text-emerald-700">
          {selectedCount} de {items.length} productos seleccionados en {stores.length}{' '}
          {stores.length === 1 ? 'local' : 'locales'}
        </p>
        <p className="text-2xl font-bold text-emerald-900">
          Total: {formatCurrency(grandTotal)}
        </p>
      </div>

      {/* Unselected items warning */}
      {selectedCount < items.length && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Faltan seleccionar:{' '}
          {items
            .filter((_, i) => !selections[i])
            .map((item, i) => (
              <span key={i}>
                {i > 0 && ', '}
                <strong>{item}</strong>
              </span>
            ))}
          .{' '}
          <button
            onClick={() => {
              const firstMissing = items.findIndex((_, i) => !selections[i]);
              if (firstMissing >= 0) setStep(firstMissing + 1);
            }}
            className="underline hover:text-amber-900"
          >
            Seleccionarlos
          </button>
        </div>
      )}

      {/* Store cards */}
      <div className="space-y-4">
        {stores.map((store) => {
          const storeTotal = store.items.reduce((sum, i) => sum + i.product.mejor_precio, 0);
          return (
            <div key={`${store.cadena}|${store.sucursal}|${store.localidad}`} className="rounded-lg border border-slate-200 bg-white overflow-hidden">
              <div className="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-center justify-between">
                <div>
                  <h3 className="font-semibold text-slate-900">{store.cadena}</h3>
                  <p className="text-sm text-slate-500">
                    {store.sucursal} — {store.localidad}, {store.provincia}
                  </p>
                </div>
                <div className="text-right">
                  <p className="text-lg font-bold text-slate-900">{formatCurrency(storeTotal)}</p>
                  <p className="text-xs text-slate-500">
                    {store.items.length} {store.items.length === 1 ? 'producto' : 'productos'}
                  </p>
                </div>
              </div>
              <div className="divide-y divide-slate-100">
                {store.items.map((item, i) => (
                  <div key={i} className="px-4 py-2.5 flex items-center justify-between text-sm">
                    <div>
                      <span className="text-xs text-slate-400 mr-2">{item.searchTerm}:</span>
                      <span className="text-slate-800">{item.product.producto}</span>
                      {item.product.marca && (
                        <span className="ml-1.5 text-slate-400">— {item.product.marca}</span>
                      )}
                    </div>
                    <div className="whitespace-nowrap">
                      <span className="font-medium text-slate-900">
                        {formatCurrency(item.product.mejor_precio)}
                      </span>
                      {item.product.precio_lista !== item.product.mejor_precio && (
                        <span className="ml-2 text-xs text-slate-400 line-through">
                          {formatCurrency(item.product.precio_lista)}
                        </span>
                      )}
                      <div><PriceDate value={item.product.fecha_precio} /></div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Helpers ──

interface ChainGroup {
  cadena: string;
  count: number;
  min_precio: number;
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
      items: items.sort((a, b) => a.mejor_precio - b.mejor_precio),
    }))
    .sort((a, b) => a.min_precio - b.min_precio);
}

// ── Main component ──

export function Lista() {
  const { currentStep, items } = useShoppingList();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Lista de Compras</h1>

      {currentStep === 0 && <BuildList />}
      {currentStep >= 1 && currentStep <= items.length && (
        <SelectProduct itemIndex={currentStep - 1} />
      )}
      {currentStep === items.length + 1 && items.length > 0 && <Summary />}
    </div>
  );
}
