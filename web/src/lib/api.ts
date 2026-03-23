import axios from 'axios';

const api = axios.create({ baseURL: '/api' });

// ── Filter endpoints ──
export const fetchProvinces = () => api.get<string[]>('/filters/provinces').then(r => r.data);
export const fetchChains = () => api.get<string[]>('/filters/chains').then(r => r.data);
export const fetchLocalities = (province: string) =>
  api.get<string[]>('/filters/localities', { params: { province } }).then(r => r.data);
export const fetchDateRange = () =>
  api.get<{ min_fecha: string | null; max_fecha: string | null }>('/filters/date-range').then(r => r.data);

// ── Products ──
export interface Product {
  producto_sk: number;
  id_producto: string;
  descripcion: string;
  marca: string | null;
}
export const searchProducts = (q: string) =>
  api.get<Product[]>('/products/search', { params: { q } }).then(r => r.data);

// ── Health ──
export interface HealthData {
  status: string;
  started_at: string | null;
  fecha_datos: string | null;
}
export const fetchHealth = () => api.get<HealthData>('/health').then(r => r.data);

// ── KPIs ──
export interface KpiData {
  inflacion_semanal: number | null;
  total_productos: number;
  total_sucursales: number;
  pct_promo: number | null;
  top_subas: { producto: string; provincia: string; variacion: number }[];
  top_bajas: { producto: string; provincia: string; variacion: number }[];
  best_chain: { cadena: string; pct_promo: number } | null;
}
export const fetchKpis = () => api.get<KpiData>('/kpis').then(r => r.data);

// ── Buscador ──
export interface BuscadorDate {
  fecha_id: number;
  fecha: string;
}
export const fetchBuscadorDates = (producto_sk: number) =>
  api.get<BuscadorDate[]>('/buscador/dates', { params: { producto_sk } }).then(r => r.data);

export interface StorePrice {
  cadena: string;
  tipo: string;
  localidad: string;
  provincia: string;
  precio_lista: number;
  mejor_precio: number;
  ahorro: number;
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
}) => api.get<StorePrice[]>('/buscador', { params }).then(r => r.data);

// ── Evolucion ──
export interface PricePoint {
  fecha: string;
  precio_lista: number | null;
  precio_promo: number | null;
  observaciones: number;
}
export const fetchEvolucion = (params: { producto_sk: number; provincia?: string }) =>
  api.get<PricePoint[]>('/evolucion', { params }).then(r => r.data);

// ── Variaciones ──
export interface VariacionItem {
  productos_descripcion: string;
  productos_marca: string | null;
  provincia: string;
  precio_anterior: number | null;
  precio_actual: number | null;
  variacion: number | null;
}
export interface VariacionesResponse {
  min_fecha: string | null;
  max_fecha: string | null;
  items: VariacionItem[];
}
export const fetchVariaciones = (params: {
  mode: 'lista' | 'promo';
  provincia?: string;
  cadenas?: string[];
}) => api.get<VariacionesResponse>('/variaciones', { params }).then(r => r.data);

// ── Ofertas ──
export interface OfertaItem {
  producto: string;
  marca: string | null;
  cadena: string;
  precio_lista: number;
  precio_promo: number;
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
  precio_promo: number;
  descuento_pct: number;
}
export const fetchStorePromos = (sucursal_sk: number) =>
  api.get<StorePromo[]>(`/locales/${sucursal_sk}/promos`).then(r => r.data);

// ── Geocode ──
export const geocode = (address: string, city: string) =>
  api.get<{ lat: number | null; lon: number | null }>('/geocode', { params: { address, city } }).then(r => r.data);
