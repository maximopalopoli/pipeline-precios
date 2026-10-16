import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import {
  fetchListTotals,
  searchProducts,
  type Product,
  type StoreListTotal,
} from '../lib/api';
import { useShoppingList } from '../hooks/useShoppingList';
import { useAddressLocation } from '../hooks/useAddressLocation';
import { LocationBar } from '../components/filters/LocationBar';
import { QueryError } from '../components/layout/QueryError';
import { formatCurrency } from '../lib/constants';
import { formatUnitPrice } from '../lib/unitPrice';
import { PriceDate } from '../components/pricing/PriceDate';
import { CoverageNotes } from '../components/pricing/CoverageNotes';

// ── Step 0: Build shopping list ──

function BuildList() {
  const { items, addItem, removeItem, clearItems, setStep } = useShoppingList();
  const [input, setInput] = useState('');

  const handleAdd = () => {
    if (input.trim().length >= 2) {
      addItem(input);
      setInput('');
    }
  };

  return (
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
  );
}

// ── Step 1..N: Select product for each item ──

const CHOICES_PER_PAGE = 20;

function SelectProduct({ itemIndex }: { itemIndex: number }) {
  const { items, selections, setSelection, setStep, nextStep, prevStep } = useShoppingList();
  const location = useAddressLocation();

  const searchTerm = items[itemIndex];
  const currentSelection = selections[itemIndex] ?? null;

  const totalItems = items.length;
  const completedCount = Object.keys(selections).length;


  // Products ranked as the search ranks them, priced at nearby stores.
  const {
    data: pages,
    isLoading,
    isError,
    error,
    refetch,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
  } = useInfiniteQuery({
    queryKey: ['lista-search', searchTerm, location.params],
    queryFn: ({ pageParam }) =>
      searchProducts({ q: searchTerm, ...location.params, limit: CHOICES_PER_PAGE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, all) => (last.hay_mas ? all.length * CHOICES_PER_PAGE : undefined),
    enabled: !!searchTerm && location.ready,
  });
  const results = pages?.pages.flatMap((page) => page.productos) ?? [];
  const nearby = location.located;

  const handleSelect = (product: Product) => {
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
            Seleccionado: <span className="font-medium">{currentSelection.nombre}</span>
            <button
              onClick={() => useShoppingList.getState().clearSelection(itemIndex)}
              className="ml-2 text-blue-500 hover:text-blue-700 underline"
            >
              Cambiar
            </button>
          </p>
        )}
      </div>

      {/* Results */}
      {isLoading && <p className="text-sm text-slate-400">Cargando resultados...</p>}

      {isError && !pages && (
        <QueryError what="los productos" error={error} onRetry={() => refetch()} />
      )}

      {pages && results.length === 0 && (
        <p className="text-sm text-slate-400">No se encontraron productos para "{searchTerm}".</p>
      )}

      {results.length > 0 && (
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {results.map((product) => {
            const isSelected = currentSelection?.producto_sk === product.producto_sk;
            return (
              <li
                key={product.producto_sk}
                className={`flex items-center justify-between gap-3 px-4 py-2.5 text-sm ${
                  isSelected ? 'bg-emerald-50' : ''
                }`}
              >
                <div className="flex-1">
                  <p className="text-slate-800">{product.nombre}</p>
                  <p className="text-xs text-slate-400">
                    {product.nombre !== product.descripcion && `${product.descripcion} · `}
                    {product.id_producto}
                  </p>
                  <CoverageNotes notes={product.cobertura} sucursales={product.sucursales} nearby={nearby} />
                </div>
                <div className="text-right whitespace-nowrap">
                  {product.precio_desde != null && product.fecha_precio_desde ? (
                    <>
                      <span className="text-slate-500">desde </span>
                      <span className="font-medium text-slate-900">
                        {formatCurrency(product.precio_desde)}
                      </span>
                      {product.precio_unitario_desde && (
                        <div className="text-xs text-slate-500">
                          {formatUnitPrice(product.precio_unitario_desde)}
                        </div>
                      )}
                      <div className="text-xs text-slate-500">
                        en {product.sucursales} {product.sucursales === 1 ? 'local' : 'locales'}
                        {nearby ? ' cerca' : ''}
                      </div>
                      <div><PriceDate value={product.fecha_precio_desde} /></div>
                    </>
                  ) : (
                    <span className="text-xs text-slate-400">
                      Ningún local{nearby ? ' cerca' : ''} lo tiene
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleSelect(product)}
                  className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
                    isSelected
                      ? 'bg-emerald-600 text-white'
                      : 'bg-slate-100 text-slate-700 hover:bg-blue-100 hover:text-blue-700'
                  }`}
                >
                  {isSelected ? '✓ Elegido' : 'Elegir'}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {hasNextPage && (
        <button
          onClick={() => fetchNextPage()}
          disabled={isFetchingNextPage}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60"
        >
          {isFetchingNextPage ? 'Cargando...' : 'Ver más productos'}
        </button>
      )}
      {isFetchNextPageError && (
        <QueryError compact what="más productos" error={error} onRetry={() => fetchNextPage()} />
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

// ── Summary: the list's total in each nearby store ──

function Summary() {
  const { items, selections, setStep, reset } = useShoppingList();
  const location = useAddressLocation();
  const { located, lat, lon, radius } = location;

  // Each chosen item stands for its product, wherever it was picked.
  const names = new Map<number, string>();
  items.forEach((term, i) => {
    const product = selections[i];
    if (product && !names.has(product.producto_sk)) {
      // A product added from the search is its own term.
      names.set(
        product.producto_sk,
        term === product.nombre ? term : `${term}: ${product.nombre}`,
      );
    }
  });
  const productIds = Array.from(names.keys());
  const selectedCount = Object.keys(selections).length;

  const { data: stores = [], isLoading, isError, error, refetch } = useQuery({
    queryKey: ['list-totals', productIds, lat, lon, radius],
    queryFn: () => fetchListTotals({ producto_sk: productIds, lat: lat!, lon: lon!, radius }),
    enabled: located && productIds.length > 0 && location.ready,
  });

  // Totals are only comparable among stores carrying the same items.
  const groups: { cubiertos: number; stores: StoreListTotal[] }[] = [];
  for (const store of stores) {
    const last = groups[groups.length - 1];
    if (last && last.cubiertos === store.cubiertos) last.stores.push(store);
    else groups.push({ cubiertos: store.cubiertos, stores: [store] });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-800">Total por local</h2>
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

      {!located && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
          Indicá dónde estás, arriba, para comparar el total de la lista en los locales cercanos.
        </div>
      )}

      {located && (
        <div className="rounded-lg border border-blue-100 bg-blue-50/50 px-4 py-2 text-sm text-blue-700">
          {productIds.length} {productIds.length === 1 ? 'producto' : 'productos'} en locales a
          menos de {radius} km de tu ubicación. El total suma el precio que se paga
          sin condiciones; las promociones condicionales se suman aparte. No incluye
          farmacias ni tiendas de estaciones de servicio.
        </div>
      )}

      {isLoading && <p className="text-sm text-slate-400">Calculando totales...</p>}
      {isError && <QueryError what="los totales de la lista" error={error} onRetry={() => refetch()} />}
      {located && !isLoading && !isError && productIds.length > 0 && stores.length === 0 && (
        <p className="text-sm text-slate-400">
          Ningún local cercano tiene precio para los productos elegidos.
        </p>
      )}

      {groups.map((group, g) => (
        <section key={group.cubiertos} className="space-y-3">
          <h3 className="text-sm font-semibold text-slate-700">
            {group.cubiertos === productIds.length
              ? `Tienen los ${productIds.length} productos`
              : `Tienen ${group.cubiertos} de ${productIds.length} productos`}
            {g > 0 && (
              <span className="ml-2 font-normal text-slate-500">
                — su total no incluye los que faltan; no se compara con los de arriba
              </span>
            )}
          </h3>
          {group.stores.map((store) => (
            <StoreTotalCard key={store.sucursal_sk} store={store} names={names} />
          ))}
        </section>
      ))}
    </div>
  );
}

function StoreTotalCard({ store, names }: { store: StoreListTotal; names: Map<number, string> }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
      <div className="px-4 py-3 bg-slate-50 border-b border-slate-200 flex items-start justify-between gap-4">
        <div>
          <h4 className="font-semibold text-slate-900">{store.cadena}</h4>
          <p className="text-sm text-slate-500">
            {store.sucursal}
            {store.direccion ? ` — ${store.direccion}` : ''}, {store.localidad} ·{' '}
            {store.distancia_km} km
          </p>
        </div>
        <div className="text-right whitespace-nowrap">
          <p className="text-lg font-bold text-slate-900">{formatCurrency(store.total)}</p>
          <p className="text-xs text-slate-500">
            {store.cubiertos} de {store.productos} productos
          </p>
          {store.total_condicional != null && (
            <p className="text-xs text-amber-700">
              {formatCurrency(store.total_condicional)} cumpliendo{' '}
              {store.condicionales === 1 ? '1 condición' : `${store.condicionales} condiciones`}
            </p>
          )}
        </div>
      </div>
      {store.faltantes.length > 0 && (
        <p className="px-4 py-2 text-sm text-amber-800 bg-amber-50 border-b border-amber-100">
          No tiene: {store.faltantes.map((sk) => names.get(sk) ?? `producto ${sk}`).join(', ')}
        </p>
      )}
      <details>
        <summary className="px-4 py-2 text-xs text-slate-500 cursor-pointer hover:text-slate-700">
          Ver precios
        </summary>
        <div className="divide-y divide-slate-100">
          {store.items.map((item) => (
            <div key={item.producto_sk} className="px-4 py-2.5 flex items-center justify-between gap-4 text-sm">
              <span className="text-slate-800">{names.get(item.producto_sk)}</span>
              <div className="text-right whitespace-nowrap">
                <span className="font-medium text-slate-900">{formatCurrency(item.mejor_precio)}</span>
                {item.precio_lista !== item.mejor_precio && (
                  <span className="ml-2 text-xs text-slate-400 line-through">
                    {formatCurrency(item.precio_lista)}
                  </span>
                )}
                {item.precio_condicional != null && (
                  <div className="text-xs text-amber-700" title={item.leyenda_condicional ?? undefined}>
                    {formatCurrency(item.precio_condicional)} con condiciones
                  </div>
                )}
                <div><PriceDate value={item.fecha_precio} /></div>
              </div>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

// ── Main component ──

export function Lista() {
  const { currentStep, items } = useShoppingList();
  const location = useAddressLocation();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Lista de Compras</h1>

      <LocationBar location={location} />

      {currentStep === 0 && <BuildList />}
      {currentStep >= 1 && currentStep <= items.length && (
        <SelectProduct itemIndex={currentStep - 1} />
      )}
      {currentStep === items.length + 1 && items.length > 0 && <Summary />}
    </div>
  );
}
