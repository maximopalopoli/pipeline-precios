import axios from 'axios';

// FastAPI reads a list from repeated keys (cadenas=A&cadenas=B), not cadenas[]=A.
const api = axios.create({ baseURL: '/api', paramsSerializer: { indexes: null } });

// ── Filter endpoints ──
export const fetchProvinces = () => api.get<string[]>('/filters/provinces').then(r => r.data);
export const fetchChains = () => api.get<string[]>('/filters/chains').then(r => r.data);
export const fetchLocalities = (province: string) =>
  api.get<string[]>('/filters/localities', { params: { province } }).then(r => r.data);

// ── Products ──
export interface Product {
  producto_sk: number;
  id_producto: string;
  es_ean: boolean;
  /** Display name from type, brand, variant and size; the description when untyped. */
  nombre: string;
  /** Description as the chains publish it (or its curated correction). */
  descripcion: string;
  marca: string | null;
  tipo: string | null;
  variante: string | null;
  contenido: number | null;
  contenido_unidad: 'g' | 'ml' | 'u' | null;
  /** Lowest store price in the latest published day; null when no nearby store carries it. */
  precio_desde: number | null;
  /** That price per kg or l; null for products sold by the unit or of unknown size. */
  precio_unitario_desde: UnitPrice | null;
  /** Observation date (YYYY-MM-DD) of that price. */
  fecha_precio_desde: string | null;
  /** Stores carrying it, only nearby ones with a location. */
  sucursales: number;
  /** Reasons its prices may compare poorly. */
  cobertura: CoverageNote[];
}
/** Why a product's prices compare poorly (api/coverage.py). */
export type CoverageNote = 'fraccionado_en_local' | 'codigo_interno' | 'pocos_locales';
/** Attributes that narrow a search to some of its products. */
export interface ProductAttributeFilters {
  tipo?: string;
  marca?: string;
  contenido?: number;
  unidad?: 'g' | 'ml' | 'u';
}
export const searchProducts = (params: ProductAttributeFilters & {
  q: string;
  /** Stores behind the from price and store count; they do not change the order. */
  provincia?: string;
  localidad?: string;
  cadenas?: string[];
  lat?: number;
  lon?: number;
  radius?: number;
  limit?: number;
  offset?: number;
}) => api.get<ProductSearchPage>('/products/search', { params }).then(r => r.data);

/** One page of a product search; ``hay_mas`` says whether another page follows. */
export interface ProductSearchPage {
  productos: Product[];
  hay_mas: boolean;
}

/** A product as the catalog names it, next to what its source published. */
export interface ProductDetail {
  producto_sk: number;
  nombre: string;
  descripcion: string;
  marca: string | null;
  tipo: string | null;
  variante: string | null;
  contenido: number | null;
  contenido_unidad: 'g' | 'ml' | 'u' | null;
  /** Chains that list the product. */
  cadenas_fuente: number | null;
  /** A curated correction replaced some published attribute. */
  corregido: boolean;
  /** Reasons its prices may compare poorly, apart from how many stores carry it. */
  cobertura: CoverageNote[];
  /** Description, brand and size as the chains wrote them. */
  publicado: {
    descripcion: string | null;
    marca: string | null;
    cantidad: string | null;
    unidad: string | null;
  };
  /** Source identifiers: an EAN, or a chain code scoped by its chain. */
  codigos: {
    identidad: string;
    id_producto: string;
    es_ean: boolean;
    id_comercio: number | null;
    id_bandera: number | null;
    cadena: string | null;
  };
}
export const fetchProduct = (productoSk: number) =>
  api.get<ProductDetail>(`/products/${productoSk}`).then(r => r.data);

export interface FacetValue {
  valor: string;
  productos: number;
}
export interface SizeFacetValue extends FacetValue {
  contenido: number;
  unidad: 'g' | 'ml' | 'u';
}
/** Choices that narrow a search; each counts with the other filters applied. */
export interface SearchFacets {
  tipos: FacetValue[];
  marcas: FacetValue[];
  tamanos: SizeFacetValue[];
}
export const fetchSearchFacets = (params: ProductAttributeFilters & { q: string }) =>
  api.get<SearchFacets>('/products/search/facets', { params }).then(r => r.data);

// ── Health ──
export interface HealthData {
  status: string;
  started_at: string | null;
  fecha_datos: string | null;
  ultima_publicacion: { fecha_datos: string; finished_at: string } | null;
  aviso_ingesta: { status: 'failed' | 'running'; fecha_datos: string; started_at: string } | null;
  reconciliacion: {
    rows_productos_raw: number | null;
    rows_productos_valid: number | null;
    rows_productos_rejected: number | null;
    rows_productos_without_keys: number | null;
    rows_productos_deduplicated: number | null;
    rows_fact_pending: number | null;
    rows_fact_published: number | null;
    rows_fact_carried_forward: number | null;
    rows_fact_snapshot: number | null;
  } | null;
}
export const fetchHealth = () => api.get<HealthData>('/health').then(r => r.data);

// ── Buscador ──
export interface BuscadorDate {
  fecha_id: number;
  fecha: string;
}
export const fetchBuscadorDates = (producto_sk: number) =>
  api.get<BuscadorDate[]>('/buscador/dates', { params: { producto_sk } }).then(r => r.data);

/** Price per kg or l, computed from the canonical package size. */
export interface UnitPrice {
  precio: number;
  unidad: 'kg' | 'l';
}

/** Requirement read from a promotion legend (migration 037). */
export type PromoCondition = 'cantidad' | 'medio_pago' | 'programa' | 'horario';

export interface StorePrice {
  cadena: string;
  tipo: string;
  localidad: string;
  provincia: string;
  precio_lista: number;
  fecha_precio: string;
  mejor_precio: number;
  ahorro: number;
  /** Promotional price only when it is lower than the list price. */
  precio_promo: number | null;
  /** Source-provided promotion conditions; null means they were not supplied. */
  leyenda_promo: string | null;
  tiene_descuento: boolean;
  /** Lower promotion that requires a condition; never part of mejor_precio. */
  precio_condicional: number | null;
  leyenda_condicional: string | null;
  condicion: PromoCondition | null;
  lat: number | null;
  lon: number | null;
  sucursal: string;
  direccion: string | null;
  /** Store price per kg or l; null when it does not apply. */
  precio_unitario: UnitPrice | null;
  /** Source store codes; id_sucursal is unique only within its chain. */
  id_comercio: number;
  id_bandera: number;
  id_sucursal: number;
}
export const fetchBuscador = (params: {
  producto_sk: number;
  fecha_id: number;
  provincia?: string;
  localidad?: string;
  cadenas?: string[];
  lat?: number;
  lon?: number;
  radius?: number;
}) => api.get<StorePrice[]>('/buscador', { params }).then(r => r.data);

// ── Generic Buscador (search by text across all matching products) ──
export interface GenericStorePrice {
  cadena: string;
  producto_sk: number;
  producto: string;
  marca: string | null;
  sucursal: string;
  tipo: string;
  localidad: string;
  provincia: string;
  precio_lista: number;
  fecha_precio: string;
  mejor_precio: number;
  precio_promo: number | null;
  leyenda_promo: string | null;
  tiene_descuento: boolean;
  /** Lower promotion that requires a condition; never part of mejor_precio. */
  precio_condicional: number | null;
  leyenda_condicional: string | null;
  condicion: PromoCondition | null;
  /** Store price per kg or l; null when it does not apply. */
  precio_unitario: UnitPrice | null;
  lat: number | null;
  lon: number | null;
}
export const fetchBuscadorGenericDates = (q: string) =>
  api.get<BuscadorDate[]>('/buscador/generic/dates', { params: { q } }).then(r => r.data);

export const fetchBuscadorGeneric = (params: {
  q: string;
  fecha_id: number;
  provincia?: string;
  localidad?: string;
  cadenas?: string[];
  lat?: number;
  lon?: number;
  radius?: number;
}) => api.get<GenericStorePrice[]>('/buscador/generic', { params }).then(r => r.data);

// ── Ofertas ──
export interface OfertaItem {
  producto: string;
  marca: string | null;
  cadena: string;
  /** Source-provided promotion conditions; null means they were not supplied. */
  leyenda_promo: string | null;
  precio_lista: number;
  fecha_precio: string;
  precio_promo: number;
  /** Requirement of this promotion; null when it states none. */
  condicion: PromoCondition | null;
  descuento_pct: number;
}
export interface OfertasResponse {
  fecha: string | null;
  items: OfertaItem[];
}
export const fetchOfertas = (params: {
  provincia?: string;
  localidad?: string;
  cadenas?: string[];
}) => api.get<OfertasResponse>('/ofertas', { params }).then(r => r.data);

// ── Locales ──
export interface NearbyStore {
  sucursal_sk: number;
  nombre: string;
  cadena: string;
  tipo: string;
  calle: string | null;
  numero: string | null;
  localidad: string;
  lat: number;
  lon: number;
  provincia: string;
  distancia_km: number;
}
export const fetchLocales = (params: {
  lat: number;
  lon: number;
  radius: number;
  cadenas?: string[];
}) => api.get<NearbyStore[]>('/locales', { params }).then(r => r.data);

export interface StorePromo {
  producto: string;
  marca: string | null;
  precio_lista: number;
  fecha_precio: string;
  precio_promo: number;
  /** Requirement of this promotion; null when it states none. */
  condicion: PromoCondition | null;
  leyenda_promo: string | null;
  descuento_pct: number;
}
export const fetchStorePromos = (sucursal_sk: number) =>
  api.get<StorePromo[]>(`/locales/${sucursal_sk}/promos`).then(r => r.data);

// ── Geocode ──
/** Radius to search around a point: the requested one, grown to reach three grocery chains. */
export interface SearchRadius {
  radio_km: number;
  /** Grocery chain companies with a priced store within radio_km. */
  cadenas: number;
  ampliado: boolean;
  /** False when even the largest radius reaches fewer than three. */
  suficiente: boolean;
}
export const fetchSearchRadius = (params: { lat: number; lon: number; radius: number }) =>
  api.get<SearchRadius>('/location/radius', { params }).then(r => r.data);

// ── Shopping list totals per nearby store ──
export interface ListItemPrice {
  producto_sk: number;
  precio_lista: number;
  mejor_precio: number;
  precio_promo: number | null;
  leyenda_promo: string | null;
  precio_condicional: number | null;
  leyenda_condicional: string | null;
  condicion: PromoCondition | null;
  fecha_precio: string;
}
export interface StoreListTotal {
  sucursal_sk: number;
  cadena: string;
  sucursal: string;
  direccion: string | null;
  localidad: string;
  lat: number | null;
  lon: number | null;
  distancia_km: number;
  /** Distinct list products the store has a price for, out of `productos`. */
  cubiertos: number;
  productos: number;
  faltantes: number[];
  /** Sum of the prices paid without meeting any condition. */
  total: number;
  /** Sum when every conditional promotion is met; null when there is none. */
  total_condicional: number | null;
  condicionales: number;
  items: ListItemPrice[];
}
/** Stores ranked by how many list products they carry, then by total. */
export const fetchListTotals = (params: {
  producto_sk: number[];
  lat: number;
  lon: number;
  radius: number;
}) => api.get<StoreListTotal[]>('/lista/totales', { params }).then(r => r.data);

export const geocode = (address: string, city: string) =>
  api.get<{ lat: number | null; lon: number | null }>('/geocode', { params: { address, city } }).then(r => r.data);
