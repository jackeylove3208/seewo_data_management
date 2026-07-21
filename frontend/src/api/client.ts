const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";
const BACKEND_UNAVAILABLE_MESSAGE = "后端服务不可用，请确认本地服务已经启动后重试";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function resolveApiUrl(path: string) {
  return `${API_BASE_URL}${path}`;
}

export const apiUrl = resolveApiUrl;

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(resolveApiUrl(path), init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(BACKEND_UNAVAILABLE_MESSAGE, 0);
  }
  if (!response.ok) {
    let message = "请求失败，请稍后重试";
    let hasServerMessage = false;
    try {
      const payload = await response.json() as { detail?: string | { message?: string } };
      if (typeof payload.detail === "string") {
        message = payload.detail;
        hasServerMessage = true;
      }
      if (typeof payload.detail === "object" && payload.detail?.message) {
        message = payload.detail.message;
        hasServerMessage = true;
      }
    } catch {
      // Keep the user-facing fallback when a proxy or server returns non-JSON.
    }
    if (!hasServerMessage && response.status >= 500) message = BACKEND_UNAVAILABLE_MESSAGE;
    throw new ApiError(message, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
