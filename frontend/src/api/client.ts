const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    let message = "请求失败，请稍后重试";
    try {
      const payload = await response.json() as { detail?: string | { message?: string } };
      if (typeof payload.detail === "string") message = payload.detail;
      if (typeof payload.detail === "object" && payload.detail?.message) message = payload.detail.message;
    } catch {
      // Keep the user-facing fallback when a proxy or server returns non-JSON.
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}
