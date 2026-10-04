import type { UnitPrice } from './api';
import { formatCurrency } from './constants';

/** "$1.250,00 por kg": price per kg or l, from the canonical package size. */
export function formatUnitPrice(value: UnitPrice): string {
  return `${formatCurrency(value.precio)} por ${value.unidad}`;
}

/**
 * Order by price per kg or l. A price per kg is never compared with one per
 * litre: each unit is ordered apart, kg first, and items without a unit
 * price go last in their original order.
 */
export function byUnitPrice<T>(items: T[], unitPrice: (item: T) => UnitPrice | null): T[] {
  const rank = (item: T) => {
    const value = unitPrice(item);
    return value ? [value.unidad === 'kg' ? 0 : 1, value.precio] : [2, 0];
  };
  return items
    .map((item, index) => ({ item, index, rank: rank(item) }))
    .sort((a, b) => a.rank[0] - b.rank[0] || a.rank[1] - b.rank[1] || a.index - b.index)
    .map(({ item }) => item);
}
