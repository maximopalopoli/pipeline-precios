import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Product } from '../lib/api';

interface ShoppingListState {
  items: string[];
  selections: Record<number, Product>; // item index → selected product
  currentStep: number; // 0 = build list, 1..N = select product, N+1 = summary
  lat: number | null;
  lon: number | null;
  radius: number;

  addItem: (term: string) => void;
  removeItem: (index: number) => void;
  clearItems: () => void;
  setSelection: (index: number, product: Product) => void;
  clearSelection: (index: number) => void;
  setStep: (step: number) => void;
  nextStep: () => void;
  prevStep: () => void;
  setLocation: (lat: number, lon: number) => void;
  setRadius: (radius: number) => void;
  clearLocation: () => void;
  reset: () => void;
}

export const useShoppingList = create<ShoppingListState>()(
  persist(
    (set) => ({
      items: [],
      selections: {},
      currentStep: 0,
      lat: null,
      lon: null,
      radius: 3,

      addItem: (term) => {
        const trimmed = term.trim();
        if (!trimmed) return;
        set((s) => ({ items: [...s.items, trimmed] }));
      },
      removeItem: (index) =>
        set((s) => {
          const items = s.items.filter((_, i) => i !== index);
          const selections = { ...s.selections };
          delete selections[index];
          // Re-index selections after removal
          const reindexed: Record<number, Product> = {};
          for (const [k, v] of Object.entries(selections)) {
            const ki = Number(k);
            reindexed[ki > index ? ki - 1 : ki] = v;
          }
          return { items, selections: reindexed, currentStep: 0 };
        }),
      clearItems: () => set({ items: [], selections: {}, currentStep: 0 }),
      setSelection: (index, product) =>
        set((s) => ({ selections: { ...s.selections, [index]: product } })),
      clearSelection: (index) =>
        set((s) => {
          const selections = { ...s.selections };
          delete selections[index];
          return { selections };
        }),
      setStep: (step) => set({ currentStep: step }),
      nextStep: () => set((s) => ({ currentStep: Math.min(s.currentStep + 1, s.items.length + 1) })),
      prevStep: () => set((s) => ({ currentStep: Math.max(s.currentStep - 1, 0) })),
      setLocation: (lat, lon) => set({ lat, lon }),
      setRadius: (radius) => set({ radius }),
      clearLocation: () => set({ lat: null, lon: null }),
      reset: () => set({ items: [], selections: {}, currentStep: 0, lat: null, lon: null, radius: 3 }),
    }),
    {
      name: 'shopping-list',
      partialize: (state) => ({ items: state.items, radius: state.radius, lat: state.lat, lon: state.lon }),
    },
  ),
);
