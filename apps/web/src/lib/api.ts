/**
 * Drake API client. Relative URLs only (same-origin via the /v1 rewrite);
 * cookies carry the opaque session, mutations carry the per-session CSRF
 * token and an Idempotency-Key. No tokens ever touch browser storage.
 */

export interface MeScope {
  scope_type: string;
  scope_ref: string;
  permissions: string[];
}

export interface Me {
  identity: { display_name: string; email: string; issuer: string };
  groups_overage: boolean;
  permissions: string[];
  scopes: Record<string, MeScope>;
  csrf_token: string;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly correlationId?: string,
  ) {
    super(message);
  }
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; correlation_id?: string };
    };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? response.statusText,
      body.error?.correlation_id,
    );
  } catch {
    return new ApiError(response.status, "error", response.statusText);
  }
}

export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  // no-store: authorization-scoped data must never outlive a permission
  // change or leak across users through shared HTTP caches.
  const response = await fetch(path, { credentials: "include", cache: "no-store", signal });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export interface MutateOptions {
  csrfToken: string;
  method?: "POST" | "PUT" | "DELETE";
  body?: unknown;
  ifMatch?: string;
  signal?: AbortSignal;
  /**
   * An idempotency key the CALLER owns.
   *
   * The default — a fresh UUID per call — is right for a request that is
   * safe to repeat and pointless to deduplicate. It is wrong for anything
   * the caller may need to RETRY: a new key on a retry describes a new
   * operation, so a request that timed out after the server committed it
   * would be applied a second time.
   *
   * Pass this when the caller holds one key across its own retries. Apply
   * does, and sends the same value in the body so the server can bind the
   * receipt to it.
   */
  idempotencyKey?: string;
}

export async function apiMutate<T>(path: string, options: MutateOptions): Promise<T> {
  const headers: Record<string, string> = {
    "X-CSRF-Token": options.csrfToken,
    "Idempotency-Key": options.idempotencyKey ?? crypto.randomUUID(),
  };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (options.ifMatch) headers["If-Match"] = options.ifMatch;

  const response = await fetch(path, {
    method: options.method ?? "POST",
    credentials: "include",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}
