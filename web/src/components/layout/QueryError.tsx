import { describeError } from '../../lib/errors';

/** A failed request: what could not load, why, and a way to try again. */
export function QueryError({
  what,
  error,
  onRetry,
  compact = false,
}: {
  what: string;
  error: unknown;
  onRetry: () => void;
  compact?: boolean;
}) {
  return (
    <div
      role="alert"
      className={
        compact
          ? 'text-sm text-red-700'
          : 'rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800'
      }
    >
      No se pudo cargar {what}. {describeError(error)}{' '}
      <button onClick={onRetry} className="font-medium underline hover:no-underline">
        Reintentar
      </button>
    </div>
  );
}
