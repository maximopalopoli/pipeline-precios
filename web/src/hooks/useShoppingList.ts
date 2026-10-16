import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { Product } from '../lib/api';

interface ShoppingListState {
  items: string[];
  selections: Record<number, Product>; // item index → selected product
  currentStep: number; // 0 = build list, 1..N = select product, N+1 = summary

  addItem: (term: string) => void;
  /** Add an item already chosen as this product, unless the list has it. */
  addProduct: (product: Product) => void;
  removeItem: (index: number) => void;
  clearItems: () => void;
  setSelection: (index: number, product: Product) => void;
  clearSelection: (index: number) => void;
  setStep: (step: number) => void;
  nextStep: () => void;
  prevStep: () => void;
  reset: () => void;
}

export const useShoppingList = create<ShoppingListState>()(
  persist(
    (set) => ({
      items: [],
      selections: {},
      currentStep: 0,

      addItem: (term) => {
        const trimmed = term.trim();
        if (!trimmed) return;
        set((s) => ({ items: [...s.items, trimmed] }));
      },
      addProduct: (product) =>
        set((s) => {
          if (Object.values(s.selections).some((p) => p.producto_sk === product.producto_sk)) return s;
          return {
            items: [...s.items, product.nombre],
            selections: { ...s.selections, [s.items.length]: product },
          };
        }),
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
      reset: () => set({ items: [], selections: {}, currentStep: 0 }),
    }),
    {
      name: 'shopping-list',
      // Selections are kept so that a product added from the search stays chosen.
      partialize: (state) => ({
        items: state.items,
        selections: state.selections,
      }),
    },
  ),
);
