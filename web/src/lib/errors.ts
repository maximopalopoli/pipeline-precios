import { isAxiosError } from 'axios';

/** What went wrong with an API request, in words a shopper can act on. */
export function describeError(error: unknown): string {
  if (isAxiosError(error)) {
    if (!error.response) return 'No hay conexión con el servidor.';
    if (error.response.status >= 500) return 'El servidor tuvo un problema.';
    if (error.response.status === 422) return 'La búsqueda tiene un valor no válido.';
  }
  return 'Ocurrió un error inesperado.';
}
