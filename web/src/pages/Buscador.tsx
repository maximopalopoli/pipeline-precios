import { useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  fetchBuscador,
  fetchBuscadorDates,
  fetchProduct,
  fetchSearchFacets,
  searchProducts,
  type FacetValue,
  type Product,
  type ProductAttributeFilters,
  type ProductDetail,
  type StorePrice,
} from '../lib/api';
import { useFilters } from '../hooks/useFilters';
import { useAddressLocation } from '../hooks/useAddressLocation';
import { useShoppingList } from '../hooks/useShoppingList';
import { RadiusNotice } from '../components/filters/RadiusNotice';
import { LocationFilter } from '../components/filters/LocationFilter';
import { FilterPanel } from '../components/filters/FilterPanel';
import { ProductSearch } from '../components/filters/ProductSearch';
import { PriceMap } from '../components/maps/PriceMap';
import { QueryError } from '../components/layout/QueryError';
import { PromotionDetails } from '../components/pricing/PromotionDetails';
import { PriceDate } from '../components/pricing/PriceDate';
import { CoverageNotes, FEW_STORES } from '../components/pricing/CoverageNotes';
import { formatCurrency } from '../lib/constants';
import { byUnitPrice, formatUnitPrice } from '../lib/unitPrice';

const CARDS_PER_PAGE = 50;
const STORES_PER_PAGE = 50;
// The API's deepest page offset; past it the search should be narrowed.
const MAX_CARDS_OFFSET = 1000;

// ── One store's offer, cheapest first; the first one is highlighted ──
function StoreOffer({ store, cheapest }: { store: StorePrice; cheapest: boolean }) {
  const reduced = store.mejor_precio < store.precio_lista;
  return (
    <li
      className={`flex items-start justify-between gap-4 rounded-lg border p-3 ${
        cheapest ? 'border-emerald-300 bg-emerald-50' : 'border-slate-200 bg-white'
      }`}
    >
      <div className="min-w-0 space-y-0.5">
        {cheapest && <p className="text-xs font-semibold text-emerald-800">Más barato</p>}
        <p className="font-medium text-slate-900">
          {store.cadena} <span className="font-normal text-slate-500">· {store.sucursal}</span>
        </p>
        <p className="text-xs text-slate-500">
          {store.direccion ? `${store.direccion}, ` : ''}{store.localidad}
        </p>
        <PromotionDetails
          tieneDescuento={store.tiene_descuento}
          leyendaPromo={store.leyenda_promo}
          precioCondicional={store.precio_condicional}
          leyendaCondicional={store.leyenda_condicional}
          condicionAparte={store.condicion}
          compact
        />
      </div>
      <div className="shrink-0 text-right">
        <p className={`text-lg font-bold ${cheapest ? 'text-emerald-800' : 'text-slate-900'}`}>
          {formatCurrency(store.mejor_precio)}
        </p>
        {reduced && (
          <p className="text-xs text-slate-400 line-through">{formatCurrency(store.precio_lista)}</p>
        )}
        {store.precio_unitario && (
          <p className="text-xs text-slate-600">{formatUnitPrice(store.precio_unitario)}</p>
        )}
        <PriceDate value={store.fecha_precio} />
      </div>
    </li>
  );
}

// ── Add the product to the shopping list, or say it is there ──
function AddToList({ product }: { product: Product }) {
  const addProduct = useShoppingList((s) => s.addProduct);
  const inList = useShoppingList((s) =>
    Object.values(s.selections).some((p) => p.producto_sk === product.producto_sk),
  );
  if (inList) {
    return (
      <p className="text-sm text-emerald-700">
        ✓ Está en tu lista ·{' '}
        <Link to="/lista" className="underline hover:text-emerald-900">Ver lista</Link>
      </p>
    );
  }
  return (
    <button
      onClick={() => addProduct(product)}
      className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
    >
      Agregar a la lista
    </button>
  );
}

// ── Product detail header: catalog name, published text and source codes ──
function ProductHeader({
  detail, product, stores, nearby,
}: { detail: ProductDetail; product: Product; stores?: number; nearby: boolean }) {
  const { publicado, codigos } = detail;
  const few = stores != null && stores > 0 && stores < FEW_STORES;
  const publishedSize = [publicado.cantidad, publicado.unidad].filter(Boolean).join(' ');
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 space-y-2">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-900">{detail.nombre}</h2>
        <AddToList product={product} />
      </div>
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

// ── Facet chips: a chosen value is clicked again to clear it ──
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
  // The type a search names is chosen before its cards load ("leche" lists
  // milk); the shopper can clear it to see every match.
  const [namedTipo, setNamedTipo] = useState<string | null>(null);
  const [resolvingType, setResolvingType] = useState(false);
  const latestSearch = useRef(0);
  const queryClient = useQueryClient();

  const [fechaId, setFechaId] = useState<number | null>(null);
  const [showMap, setShowMap] = useState(false);
  const [sortByUnitPrice, setSortByUnitPrice] = useState(false);

  // ── Location filter ──
  const location = useAddressLocation();
  const userLat = location.lat;
  const userLon = location.lon;

  const handleSelectProduct = (p: Product | null) => {
    latestSearch.current += 1;
    setResolvingType(false);
    setProduct(p);
    setGenericQuery(null);
    setFechaId(null);
  };

  const handleSearchAll = async (q: string) => {
    const search = ++latestSearch.current;
    setGenericQuery(q || null);
    setAttributes({});
    setNamedTipo(null);
    setProduct(null);
    setFechaId(null);
    if (!q) {
      setResolvingType(false);
      return;
    }
    setResolvingType(true);
    let tipo: string | null = null;
    try {
      const facets = await queryClient.fetchQuery({
        queryKey: ['search-facets', q, {}],
        queryFn: () => fetchSearchFacets({ q }),
      });
      tipo = facets.tipo_nombrado;
    } catch {
      // Without the facets the cards still list every match.
    }
    if (search !== latestSearch.current) return;
    setNamedTipo(tipo);
    if (tipo) setAttributes({ tipo });
    setResolvingType(false);
  };

  // ── Dates ──
  const { data: dates = [], isSuccess: datesLoaded, error: datesError, refetch: refetchDates } = useQuery({
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
  const searchRadius = location.radius;
  const locationParams = location.params;

  // ── Single-product results ──
  // Cheapest first, a page at a time; the summary covers every store.
  const {
    data: storePages,
    isLoading: isLoadingSingle,
    error: storesError,
    refetch: refetchStores,
    fetchNextPage: fetchMoreStores,
    hasNextPage: hasMoreStores,
    isFetchingNextPage: isFetchingMoreStores,
    isFetchNextPageError: isMoreStoresError,
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
    enabled: !!product && !!selectedFechaId && location.ready,
  });
  const stores = storePages?.pages.flatMap((page) => page.sucursales) ?? [];
  const storeSummary = storePages?.pages[0];

  // ── Product cards of a search (latest published day) ──
  // The panel and location choose the stores behind each card's price.
  // Pages follow the search's ranking; "Ver más" appends the next one.
  const {
    data: cardPages,
    isLoading: isLoadingCards,
    error: cardsError,
    refetch: refetchCards,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError: isMoreCardsError,
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
    enabled: !!genericQuery && !resolvingType && location.ready,
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
    enabled: !!genericQuery && !resolvingType,
    placeholderData: (previous) => previous,
  });

  const toggleAttributes = (selected: boolean, chosen: ProductAttributeFilters) =>
    setAttributes((current) => {
      const cleared = { ...current };
      for (const key of Object.keys(chosen) as (keyof ProductAttributeFilters)[]) delete cleared[key];
      return selected ? cleared : { ...cleared, ...chosen };
    });

  // Brands add up, to compare them; clicking a chosen brand removes it.
  const toggleBrand = (brand: string) =>
    setAttributes((current) => {
      const brands = current.marca ?? [];
      const marca = brands.includes(brand) ? brands.filter((b) => b !== brand) : [...brands, brand];
      return { ...current, marca: marca.length > 0 ? marca : undefined };
    });

  const isLoading = isLoadingSingle || isLoadingCards || resolvingType;
  const isGenericMode = !!genericQuery;
  const nearby = userLat != null && userLon != null;
  const storesChosen = nearby || !!provincia || !!localidad || cadenas.length > 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Compará precios cerca tuyo</h1>
        <p className="text-slate-500">
          Indicá dónde estás y buscá un producto para ver en qué supermercados cercanos está más barato.
        </p>
      </div>

      <div className="grid md:grid-cols-[280px_1fr] gap-6">
        {/* Location first, then the search, then the narrower filters */}
        <div className="space-y-4">
          <LocationFilter location={location} title="Tu ubicación" />
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

          <FilterPanel showLocality />
        </div>

        {/* Results */}
        <div className="space-y-4">
          {!product && !genericQuery && (
            <p className="text-sm text-slate-400">
              Escribí un producto, por ejemplo "leche", y presioná Enter para ver todos los resultados,
              o elegí uno de la lista para ver sus precios por local.
            </p>
          )}

          {isLoading && <p className="text-sm text-slate-400">Cargando...</p>}

          {!isGenericMode && product && datesError && (
            <QueryError what="las fechas del producto" error={datesError} onRetry={() => refetchDates()} />
          )}
          {!isGenericMode && storesError && !storePages && (
            <QueryError what="los precios del producto" error={storesError} onRetry={() => refetchStores()} />
          )}
          {isGenericMode && cardsError && !cardPages && (
            <QueryError what="los productos" error={cardsError} onRetry={() => refetchCards()} />
          )}

          {/* Location active indicator */}
          {location.located && (product || genericQuery) && !isLoading && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-800">
              Mostrando resultados a menos de {searchRadius} km de tu ubicacion
              {((isGenericMode && cards.length > 0 && cards.every((c) => c.sucursales === 0))
                || (!isGenericMode && storePages && stores.length === 0)) && (
                <span> — no hay locales cerca. Intenta aumentar el radio.</span>
              )}
              <RadiusNotice area={location.area} />
            </div>
          )}

          {/* ── Single-product detail ── */}
          {!isGenericMode && detail && detail.producto_sk === product?.producto_sk && (
            <ProductHeader
              detail={detail}
              product={product}
              stores={isLoadingSingle ? undefined : storeSummary?.total}
              nearby={nearby}
            />
          )}

          {!isGenericMode && product && datesLoaded && dates.length === 0 && (
            <p className="text-sm text-slate-400">Este producto no tiene precios publicados.</p>
          )}
          {!isGenericMode && storePages && stores.length === 0 && !location.located && (
            <p className="text-sm text-slate-400">No hay precios de este producto para los filtros elegidos.</p>
          )}

          {/* ── Single-product results: stores cheapest first ── */}
          {!isGenericMode && stores.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-lg font-semibold text-slate-800">
                  {nearby ? 'Dónde está más barato cerca tuyo' : 'Dónde está más barato'}
                </h2>
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
              <ol className="space-y-2">
                {stores.map((store, i) => (
                  <StoreOffer key={store.sucursal_sk} store={store} cheapest={i === 0} />
                ))}
              </ol>
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
                {isMoreStoresError && (
                  <QueryError compact what="más locales" error={storesError} onRetry={() => fetchMoreStores()} />
                )}
              </div>
            </div>
          )}

          {/* ── The type the search names, chosen for the shopper ── */}
          {isGenericMode && !resolvingType && namedTipo && attributes.tipo === namedTipo && (
            <div className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700">
              Mostrando solo <strong>{namedTipo}</strong>.{' '}
              <button
                onClick={() => toggleAttributes(true, { tipo: namedTipo })}
                className="text-blue-700 underline hover:text-blue-900"
              >
                Ver todo lo que coincide con "{genericQuery}"
              </button>
            </div>
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
                isSelected={(v) => attributes.marca?.includes(v.valor) ?? false}
                onToggle={(v) => toggleBrand(v.valor)}
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
              {isMoreCardsError && (
                <QueryError compact what="más resultados" error={cardsError} onRetry={() => fetchNextPage()} />
              )}
              {moreCards && !hasNextPage && (
                <span>Usá los filtros de tipo, marca o tamaño para acotar la búsqueda.</span>
              )}
            </div>
          )}

          {isGenericMode && cardPages && cards.length === 0 && (
            <p className="text-sm text-slate-400">
              No se encontraron productos para "{genericQuery}".
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
