import { useQuery } from '@tanstack/react-query';
import { fetchHealth } from '../../lib/api';
import { formatPriceDate, priceAgeDays, STALE_AFTER_DAYS } from '../../lib/priceDate';

/**
 * Says on every page when the published prices may mislead: none yet, a day
 * older than STALE_AFTER_DAYS, or a later ingestion that failed or still runs.
 */
export function DataNotice() {
  const { data: health } = useQuery({
    queryKey: ['health'], queryFn: fetchHealth, refetchInterval: 60_000,
  });
  if (!health) return null;

  const published = health.ultima_publicacion?.fecha_datos ?? null;
  const ageDays = published ? priceAgeDays(published) : null;
  const notice = health.aviso_ingesta;
  const messages: string[] = [];
  if (!published) {
    messages.push('Todavía no hay precios publicados.');
  } else if (ageDays! > STALE_AFTER_DAYS) {
    messages.push(
      `Los últimos precios publicados son del ${formatPriceDate(published)}, hace ${ageDays} días; pueden no estar al día.`,
    );
  }
  if (notice) {
    messages.push(
      `La ingesta del ${formatPriceDate(notice.fecha_datos)} ${notice.status === 'failed' ? 'falló' : 'está en curso'}.`
      + (published ? ' Se siguen mostrando los últimos precios publicados.' : ''),
    );
  }
  if (messages.length === 0) return null;

  const failed = notice?.status === 'failed';
  return (
    <div
      role="status"
      className={`mb-4 rounded-md border px-3 py-2 text-sm ${
        failed ? 'border-red-200 bg-red-50 text-red-800' : 'border-amber-200 bg-amber-50 text-amber-800'
      }`}
    >
      {messages.join(' ')}
    </div>
  );
}
