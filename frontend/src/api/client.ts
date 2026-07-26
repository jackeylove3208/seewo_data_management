const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";
const BACKEND_UNAVAILABLE_MESSAGE = "后端服务不可用，请确认本地服务已经启动后重试";
const BACKEND_INTERNAL_ERROR_MESSAGE = "后端处理请求失败，请查看后端终端日志后重试";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
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
    let code: string | undefined;
    let hasServerMessage = false;
    try {
      const payload = await response.json() as {
        detail?: string | { code?: string; message?: string };
      };
      if (typeof payload.detail === "string") {
        message = payload.detail;
        hasServerMessage = true;
      }
      if (typeof payload.detail === "object" && payload.detail) {
        code = payload.detail.code;
        if (payload.detail.message) {
          message = payload.detail.message;
          hasServerMessage = true;
        }
      }
    } catch {
      // Keep the user-facing fallback when a proxy or server returns non-JSON.
    }
    if (!hasServerMessage && response.status >= 500) {
      message = response.status >= 502
        ? BACKEND_UNAVAILABLE_MESSAGE
        : BACKEND_INTERNAL_ERROR_MESSAGE;
    }
    throw new ApiError(message, response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
