import { create } from 'zustand';

interface FilterState {
  provincia: string | null;
  localidad: string | null;
  cadenas: string[];
  setProvince: (p: string | null) => void;
  setLocality: (l: string | null) => void;
  setChains: (c: string[]) => void;
  reset: () => void;
}

export const useFilters = create<FilterState>((set) => ({
  provincia: null,
  localidad: null,
  cadenas: [],
  setProvince: (provincia) => set({ provincia, localidad: null }),
  setLocality: (localidad) => set({ localidad }),
  setChains: (cadenas) => set({ cadenas }),
  reset: () => set({ provincia: null, localidad: null, cadenas: [] }),
}));
