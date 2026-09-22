interface PromotionDetailsProps {
  tieneDescuento: boolean;
  leyendaPromo: string | null;
  compact?: boolean;
}

/** Explain whether a displayed price requires a source-reported promotion. */
export function PromotionDetails({
  tieneDescuento,
  leyendaPromo,
  compact = false,
}: PromotionDetailsProps) {
  if (!tieneDescuento) {
    return <span className="text-xs text-slate-500">Precio de lista</span>;
  }

  if (!leyendaPromo) {
    return (
      <span className="text-xs text-amber-700">
        Con promoción — condiciones no informadas
      </span>
    );
  }

  return (
    <span className={compact ? 'text-xs text-emerald-700' : 'text-sm text-emerald-700'}>
      Con promoción: {leyendaPromo}
    </span>
  );
}
