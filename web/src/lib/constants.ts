export const COLORS = {
  precioLista: '#3498db',
  precioPromo: '#e67e22',
  suba: '#e74c3c',
  baja: '#2ecc71',
  promo1: '#3498db',
  promo2: '#9b59b6',
} as const;

export const formatCurrency = (value: number) =>
  `$${value.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const formatPercent = (value: number) =>
  `${value.toFixed(1)}%`;
