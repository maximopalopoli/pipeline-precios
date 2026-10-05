import type { PromoCondition } from '../../lib/api';
import { formatCurrency } from '../../lib/constants';

const CONDITION_LABELS: Record<PromoCondition, string> = {
  cantidad: 'llevando varias unidades',
  medio_pago: 'con un medio de pago',
  programa: 'con programa de clientes',
  horario: 'en cierto horario',
};

interface PromotionDetailsProps {
  tieneDescuento: boolean;
  leyendaPromo: string | null;
  /** Requirement of the promotion described by leyendaPromo, if any. */
  condicion?: PromoCondition | null;
  /** A conditional promotion shown apart from the store price. */
  precioCondicional?: number | null;
  leyendaCondicional?: string | null;
  condicionAparte?: PromoCondition | null;
  compact?: boolean;
}

/** Explain whether a displayed price requires a source-reported promotion. */
export function PromotionDetails({
  tieneDescuento,
  leyendaPromo,
  condicion = null,
  precioCondicional = null,
  leyendaCondicional = null,
  condicionAparte = null,
  compact = false,
}: PromotionDetailsProps) {
  const size = compact ? 'text-xs' : 'text-sm';
  let main;
  if (!tieneDescuento) {
    main = <span className="text-xs text-slate-500">Precio de lista</span>;
  } else if (condicion) {
    main = (
      <span className={`${size} text-amber-700`}>
        Promoción condicional ({CONDITION_LABELS[condicion]}): {leyendaPromo}
      </span>
    );
  } else if (!leyendaPromo) {
    main = (
      <span className="text-xs text-amber-700">
        Con promoción — condiciones no informadas
      </span>
    );
  } else {
    main = <span className={`${size} text-emerald-700`}>Con promoción: {leyendaPromo}</span>;
  }

  if (precioCondicional == null) {
    return main;
  }
  return (
    <span className="flex flex-col">
      {main}
      <span className="text-xs text-amber-700">
        {formatCurrency(precioCondicional)}{' '}
        {condicionAparte ? CONDITION_LABELS[condicionAparte] : 'con condiciones'}
        {leyendaCondicional && ` — ${leyendaCondicional}`} (no incluido en el precio)
      </span>
    </span>
  );
}
