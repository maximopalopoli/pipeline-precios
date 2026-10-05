import { useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { createColumnHelper } from '@tanstack/react-table';
import {
  fetchBuscador,
  fetchBuscadorDates,
  fetchProduct,
  fetchSearchFacets,
  geocode,
  searchProducts,
  type FacetValue,
  type Product,
  type ProductAttributeFilters,
  type ProductDetail,
  type StorePrice,
} from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { useSearchRadius } from '../hooks/useSearchRadius';
import { RadiusNotice } from '../components/filters/RadiusNotice';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { DataTable } from '../components/tables/DataTable';
import { PriceMap } from '../components/maps/PriceMap';
import { KpiCard } from '../components/layout/KpiCard';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';
import { CoverageNotes, FEW_STORES } from '../components/pricing/CoverageNotes';
import { formatCurrency } from '../lib/constants';
import { byUnitPrice, formatUnitPrice } from '../lib/unitPrice';

// ── Single-product table columns ──
const CARDS_PER_PAGE = 50;
const STORES_PER_PAGE = 50;
// The API's deepest page offset; past it the search should be narrowed.
const MAX_CARDS_OFFSET = 1000;

const col = createColumnHelper<StorePrice>();
const columns = [
  col.accessor('cadena', { header: 'Cadena' }),
  col.accessor('sucursal', {
    header: 'Sucursal',
    cell: (info) => (
      <>
        <span>{info.getValue()}</span>
        {info.row.original.direccion && (
          <span className="block text-xs text-slate-400">{info.row.original.direccion}</span>
        )}
      </>
    ),
  }),
  col.accessor('tipo', { header: 'Tipo' }),
  col.accessor('localidad', { header: 'Localidad' }),
  col.accessor('provincia', { header: 'Provincia' }),
  col.accessor('precio_lista', { header: 'Precio lista', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('mejor_precio', { header: 'Mejor precio', cell: (info) => formatCurrency(info.getValue()) }),
  col.accessor('precio_unitario', {
    header: 'Por kg / l',
    cell: (info) => {
      const value = info.getValue();
      return value ? formatUnitPrice(value) : '—';
    },
    enableSorting: false,
  }),
  col.accessor('fecha_precio', { header: 'Fecha', cell: (info) => <PriceDate value={info.getValue()} /> }),
  col.accessor('ahorro', { header: 'Ahorro', cell: (info) => formatCurrency(info.getValue()) }),
  col.display({
    id: 'promocion',
    header: 'Condiciones',
    cell: (info) => (
      <PromotionDetails
        tieneDescuento={info.row.original.tiene_descuento}
        leyendaPromo={info.row.original.leyenda_promo}
        precioCondicional={info.row.original.precio_condicional}
        leyendaCondicional={info.row.original.leyenda_condicional}
        condicionAparte={info.row.original.condicion}
        compact
      />
    ),
  }),
  col.display({
    id: 'codigos',
    header: 'Códigos',
    cell: (info) => {
      const { id_comercio, id_bandera, id_sucursal } = info.row.original;
      return (
        <span className="font-mono text-xs text-slate-500" title="comercio-bandera-sucursal">
          {id_comercio}-{id_bandera}-{id_sucursal}
        </span>
      );
    },
  }),
];

// ── Product detail header: catalog name, published text and source codes ──
function ProductHeader({ detail, stores, nearby }: { detail: ProductDetail; stores?: number; nearby: boolean }) {
  const { publicado, codigos } = detail;
  const few = stores != null && stores > 0 && stores < FEW_STORES;
  const publishedSize = [publicado.cantidad, publicado.unidad].filter(Boolean).join(' ');
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-2">
      <h2 className="text-lg font-semibold text-slate-900">{detail.nombre}</h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-slate-500">Publicado como</dt>
        <dd className="text-slate-800">
          {publicado.descripcion ?? '—'}
          {publicado.marca && ` · ${publicado.marca}`}
          {publishedSize && ` · ${publishedSize}`}
          {detail.corregido && (
            <span className="ml-2 text-xs text-amber-700">(el nombre usa una corrección del catálogo)</span>
          )}
        </dd>
        <dt className="text-slate-500">{codigos.es_ean ? 'EAN' : 'Código de cadena'}</dt>
        <dd className="font-mono text-slate-800">
          {codigos.id_producto}
          {!codigos.es_ean && codigos.cadena && (
            <span className="ml-2 font-sans text-xs text-slate-500">
              solo identifica el producto en {codigos.cadena} ({codigos.id_comercio}-{codigos.id_bandera})
            </span>
          )}
        </dd>
        {detail.cadenas_fuente != null && (
          <>
            <dt className="text-slate-500">Cadenas</dt>
            <dd className="text-slate-800">{detail.cadenas_fuente}</dd>
          </>
        )}
      </dl>
      <CoverageNotes
        notes={few ? [...detail.cobertura, 'pocos_locales'] : detail.cobertura}
        sucursales={stores}
        cadena={codigos.cadena}
        nearby={nearby}
      />
    </div>
  );
}

// ── Facet chips: one choice per facet, clicked again to clear ──
function FacetChips<T extends FacetValue>({
  label,
  values,
  isSelected,
  onToggle,
}: {
  label: string;
  values: T[];
  isSelected: (value: T) => boolean;
  onToggle: (value: T) => void;
}) {
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs font-medium text-slate-500 mr-1">{label}</span>
      {values.map((value) => {
        const selected = isSelected(value);
        return (
          <button
            key={value.valor}
            onClick={() => onToggle(value)}
            aria-pressed={selected}
            className={`rounded-full border px-2.5 py-0.5 text-xs transition-colors ${
              selected
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-slate-300 bg-white text-slate-700 hover:border-blue-300'
            }`}
          >
            {value.valor} <span className={selected ? 'text-blue-100' : 'text-slate-400'}>{value.productos}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Buscador() {
  const { provincia, localidad, cadenas } = useFilters();

  // Two mutually exclusive modes: one product, or the cards of a search
  const [product, setProduct] = useState<Product | null>(null);
  const [genericQuery, setGenericQuery] = useState<string | null>(null);
  const [attributes, setAttributes] = useState<ProductAttributeFilters>({});

  const [fechaId, setFechaId] = useState<number | null>(null);
  const [showMap, setShowMap] = useState(false);
  const [sortByUnitPrice, setSortByUnitPrice] = useState(false);

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
  };

  const handleSearchAll = (q: string) => {
    setGenericQuery(q || null);
    setAttributes({});
    setProduct(null);
    setFechaId(null);
  };

  // ── Dates ──
  const { data: dates = [] } = useQuery({
    queryKey: ['buscador-dates', product?.producto_sk],
    queryFn: () => fetchBuscadorDates(product!.producto_sk),
    enabled: !!product,
  });

  const { data: detail } = useQuery({
    queryKey: ['product', product?.producto_sk],
    queryFn: () => fetchProduct(product!.producto_sk),
    enabled: !!product,
  });

  const activeDates = product ? dates : [];
  const selectedFechaId = fechaId ?? (activeDates.length > 0 ? activeDates[0].fecha_id : null);

  // ── Location params for API: the chosen radius, grown to reach three chains ──
  const searchArea = useSearchRadius(userLat, userLon, radius);
  const searchRadius = searchArea.radius;
  const locationParams = userLat != null && userLon != null
    ? { lat: userLat, lon: userLon, radius: searchRadius } : {};

  // ── Single-product results ──
  // Cheapest first, a page at a time; the summary covers every store.
  const {
    data: storePages,
    isLoading: isLoadingSingle,
    fetchNextPage: fetchMoreStores,
    hasNextPage: hasMoreStores,
    isFetchingNextPage: isFetchingMoreStores,
  } = useInfiniteQuery({
    queryKey: ['buscador', product?.producto_sk, selectedFechaId, provincia, localidad, cadenas, userLat, userLon, searchRadius],
    queryFn: ({ pageParam }) =>
      fetchBuscador({
        producto_sk: product!.producto_sk,
        fecha_id: selectedFechaId!,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
        limit: STORES_PER_PAGE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) =>
      last.hay_mas ? pages.length * STORES_PER_PAGE : undefined,
    enabled: !!product && !!selectedFechaId && searchArea.ready,
  });
  const stores = storePages?.pages.flatMap((page) => page.sucursales) ?? [];
  const storeSummary = storePages?.pages[0];

  // ── Product cards of a search (latest published day) ──
  // The panel and location choose the stores behind each card's price.
  // Pages follow the search's ranking; "Ver más" appends the next one.
  const {
    data: cardPages,
    isLoading: isLoadingCards,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteQuery({
    queryKey: ['product-cards', genericQuery, attributes, provincia, localidad, cadenas, userLat, userLon, searchRadius],
    queryFn: ({ pageParam }) =>
      searchProducts({
        q: genericQuery!,
        ...attributes,
        provincia: provincia || undefined,
        localidad: localidad || undefined,
        cadenas: cadenas.length > 0 ? cadenas : undefined,
        ...locationParams,
        limit: CARDS_PER_PAGE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const offset = pages.length * CARDS_PER_PAGE;
      return last.hay_mas && offset <= MAX_CARDS_OFFSET ? offset : undefined;
    },
    enabled: !!genericQuery && searchArea.ready,
  });
  const cards = cardPages?.pages.flatMap((page) => page.productos) ?? [];
  const moreCards = cardPages?.pages.at(-1)?.hay_mas ?? false;

  // Sizes are compared per kg or l, among the cards loaded.
  const comparable = cards.some((card) => card.precio_unitario_desde);
  const shownCards = sortByUnitPrice
    ? byUnitPrice(cards, (card) => card.precio_unitario_desde)
    : cards;

  const { data: facets } = useQuery({
    queryKey: ['search-facets', genericQuery, attributes],
    queryFn: () => fetchSearchFacets({ q: genericQuery!, ...attributes }),
    enabled: !!genericQuery,
    placeholderData: (previous) => previous,
  });

  const toggleAttributes = (selected: boolean, chosen: ProductAttributeFilters) =>
    setAttributes((current) => {
      const cleared = { ...current };
      for (const key of Object.keys(chosen) as (keyof ProductAttributeFilters)[]) delete cleared[key];
      return selected ? cleared : { ...cleared, ...chosen };
    });

  const isLoading = isLoadingSingle || isLoadingCards;
  const isGenericMode = !!genericQuery;
  const nearby = userLat != null && userLon != null;
  const storesChosen = nearby || !!provincia || !!localidad || cadenas.length > 0;
  const cheapest = !isGenericMode && stores.length > 0 ? stores[0] : null;

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
              Mostrando resultados a menos de {searchRadius} km de tu ubicacion
              {((isGenericMode && cards.length > 0 && cards.every((c) => c.sucursales === 0))
                || (!isGenericMode && stores.length === 0 && selectedFechaId)) && (
                <span> — no hay locales cerca. Intenta aumentar el radio.</span>
              )}
              <RadiusNotice area={searchArea.area} />
            </div>
          )}

          {/* ── Single-product detail ── */}
          {!isGenericMode && detail && detail.producto_sk === product?.producto_sk && (
            <ProductHeader
              detail={detail}
              stores={isLoadingSingle ? undefined : storeSummary?.total}
              nearby={nearby}
            />
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
                  precioCondicional={cheapest.precio_condicional}
                  leyendaCondicional={cheapest.leyenda_condicional}
                  condicionAparte={cheapest.condicion}
                />
                <PriceDate value={cheapest.fecha_precio} />
              </div>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiCard label="Locales" value={String(storeSummary?.total ?? stores.length)} />
                <KpiCard label="Precio min." value={formatCurrency(storeSummary?.precio_min ?? cheapest.mejor_precio)} />
                <KpiCard label="Precio max." value={formatCurrency(storeSummary?.precio_max ?? cheapest.mejor_precio)} />
                <KpiCard
                  label="Precio prom."
                  value={formatCurrency(storeSummary?.precio_promedio ?? cheapest.mejor_precio)}
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
                <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-slate-600">
                  <span>
                    {stores.length === storeSummary?.total
                      ? `${stores.length} ${stores.length === 1 ? 'local' : 'locales'}`
                      : `Los ${stores.length} más baratos de ${storeSummary?.total} locales`}
                    {showMap && hasMoreStores ? '; el mapa muestra solo estos' : ''}
                  </span>
                  {hasMoreStores && (
                    <button
                      onClick={() => fetchMoreStores()}
                      disabled={isFetchingMoreStores}
                      className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                    >
                      {isFetchingMoreStores ? 'Cargando...' : 'Ver más locales'}
                    </button>
                  )}
                </div>
              </div>
            </>
          )}

          {/* ── Facets ── */}
          {isGenericMode && facets && (
            <div className="space-y-2">
              <FacetChips
                label="Tipo"
                values={facets.tipos}
                isSelected={(v) => attributes.tipo === v.valor}
                onToggle={(v) => toggleAttributes(attributes.tipo === v.valor, { tipo: v.valor })}
              />
              <FacetChips
                label="Marca"
                values={facets.marcas}
                isSelected={(v) => attributes.marca === v.valor}
                onToggle={(v) => toggleAttributes(attributes.marca === v.valor, { marca: v.valor })}
              />
              <FacetChips
                label="Tamaño"
                values={facets.tamanos}
                isSelected={(v) => attributes.contenido === v.contenido && attributes.unidad === v.unidad}
                onToggle={(v) =>
                  toggleAttributes(
                    attributes.contenido === v.contenido && attributes.unidad === v.unidad,
                    { contenido: v.contenido, unidad: v.unidad },
                  )
                }
              />
            </div>
          )}

          {/* ── Product cards ── */}
          {isGenericMode && comparable && (
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input
                type="checkbox"
                checked={sortByUnitPrice}
                onChange={(e) => setSortByUnitPrice(e.target.checked)}
                className="rounded"
              />
              Ordenar estos resultados por precio por kg o litro
            </label>
          )}
          {isGenericMode && cards.length > 0 && (
            <div className="grid sm:grid-cols-2 gap-3">
              {shownCards.map((card) => (
                <button
                  key={card.producto_sk}
                  onClick={() => handleSelectProduct(card)}
                  className="rounded-lg border border-slate-200 bg-white p-4 text-left hover:border-blue-300 hover:bg-blue-50/40 transition-colors"
                >
                  <p className="font-medium text-slate-900">{card.nombre}</p>
                  <p className="text-xs text-slate-400">
                    {card.nombre !== card.descripcion && `${card.descripcion} · `}{card.id_producto}
                  </p>
                  {card.precio_desde != null && card.fecha_precio_desde ? (
                    <>
                      <p className="mt-2 text-sm text-slate-600">
                        desde{' '}
                        <span className="text-lg font-bold text-emerald-700">
                          {formatCurrency(card.precio_desde)}
                        </span>
                      </p>
                      {card.precio_unitario_desde && (
                        <p className="text-xs text-slate-600">
                          {formatUnitPrice(card.precio_unitario_desde)}
                        </p>
                      )}
                      <p className="text-xs text-slate-500">
                        en {card.sucursales} {card.sucursales === 1 ? 'local' : 'locales'}
                        {storesChosen && (nearby ? ' cerca' : ' de tu selección')}
                      </p>
                      <PriceDate value={card.fecha_precio_desde} />
                    </>
                  ) : (
                    <p className="mt-2 text-sm text-slate-400">
                      Ningún local{nearby ? ' cerca' : storesChosen ? ' de tu selección' : ''} lo tiene.
                    </p>
                  )}
                  <CoverageNotes notes={card.cobertura} sucursales={card.sucursales} nearby={nearby} />
                </button>
              ))}
            </div>
          )}

          {isGenericMode && cards.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 text-sm text-slate-600">
              <span>
                {cards.length} {cards.length === 1 ? 'producto' : 'productos'}
                {moreCards ? ' — hay más' : ''}
              </span>
              {hasNextPage && (
                <button
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {isFetchingNextPage ? 'Cargando...' : 'Ver más resultados'}
                </button>
              )}
              {moreCards && !hasNextPage && (
                <span>Usá los filtros de tipo, marca o tamaño para acotar la búsqueda.</span>
              )}
            </div>
          )}

          {isGenericMode && !isLoading && cards.length === 0 && (
            <p className="text-sm text-slate-400">
              No se encontraron productos para "{genericQuery}".
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
