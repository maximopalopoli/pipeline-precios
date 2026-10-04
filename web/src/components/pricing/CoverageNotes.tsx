import type { CoverageNote } from '../../lib/api';

/** Below this many stores a product is "carried by few stores" (api/coverage.py). */
export const FEW_STORES = 3;

interface CoverageNotesProps {
  notes: CoverageNote[];
  /** Stores carrying the product, for the few-stores note. */
  sucursales?: number;
  /** Chain that scopes the product's code, when known. */
  cadena?: string | null;
  nearby?: boolean;
}

/** Explains why a product's prices compare poorly. */
export function CoverageNotes({ notes, sucursales, cadena, nearby = false }: CoverageNotesProps) {
  if (notes.length === 0) return null;
  const chain = cadena ? `de ${cadena}` : 'de la cadena';
  const text: Record<CoverageNote, string> = {
    fraccionado_en_local:
      'Fresco o fraccionado en el local: cada cadena le pone su propio código, así que su precio no se compara entre cadenas.',
    codigo_interno: `Sin código de barras: es un código propio ${chain} y no se compara con otras cadenas.`,
    pocos_locales: `Solo ${sucursales ?? 'unos pocos'} ${sucursales === 1 ? 'local' : 'locales'}${
      nearby ? ' cerca' : ''
    } lo ${sucursales === 1 ? 'informa' : 'informan'} a SEPA.`,
  };
  return (
    <ul className="space-y-0.5">
      {notes.map((note) => (
        <li key={note} className="text-xs text-amber-700">
          {text[note]}
        </li>
      ))}
    </ul>
  );
}
