import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

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
  /** Observation date (YYYY-MM-DD) of that price. */
  fecha_precio_desde: string | null;
  /** Stores carrying it, only nearby ones with a location. */
  sucursales: number;
}
export const searchProducts = (params: {
  q: string;
  lat?: number;
  lon?: number;
  radius?: number;
  limit?: number;
}) => api.get<Product[]>('/products/search', { params }).then(r => r.data);

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

// ── EAN Buscador (compare same product across chains) ──
export interface EanStorePrice {
  producto_sk: number;
  id_producto: string;
  producto: string;
  marca: string | null;
  cantidad: string | null;
  unidad: string | null;
  cadena: string;
  sucursal_sk: number;
  sucursal: string;
  tipo: string;
  calle: string | null;
  numero: string | null;
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
  lat: number | null;
  lon: number | null;
}
export const fetchBuscadorEanDates = (q: string) =>
  api.get<BuscadorDate[]>('/buscador/ean/dates', { params: { q } }).then(r => r.data);

export const fetchBuscadorEan = (params: {
  q: string;
  fecha_id: number;
  provincia?: string;
  localidad?: string;
  cadenas?: string[];
  lat?: number;
  lon?: number;
  radius?: number;
}) => api.get<EanStorePrice[]>('/buscador/ean', { params }).then(r => r.data);

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
export const geocode = (address: string, city: string) =>
  api.get<{ lat: number | null; lon: number | null }>('/geocode', { params: { address, city } }).then(r => r.data);
