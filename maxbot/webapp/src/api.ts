export interface ChatEntry {
  chat_id: string;
  chat_type: "dm" | "group" | "channel" | "unknown";
  profile: string;
}
export interface ProfileEntry {
  name: string;
  description: string;
}
export interface StateResponse {
  chats: ChatEntry[];
  profiles: ProfileEntry[];
  me: { user_id: number; is_admin: boolean };
}

declare global {
  interface Window {
    WebApp?: { initData?: string };
  }
}

function devUserId(): string | null {
  return new URLSearchParams(window.location.search).get("dev_user_id");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const initData = window.WebApp?.initData;
  if (initData) headers["X-WebApp-InitData"] = initData;
  const dev = devUserId();
  const url = dev ? `${path}${path.includes("?") ? "&" : "?"}dev_user_id=${dev}` : path;
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) throw new Error("Нужна авторизация: откройте приложение в MAX");
  if (res.status === 403) throw new Error("Нет доступа к управлению профилями");
  if (!res.ok) throw new Error(`Ошибка ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  state: () => request<StateResponse>("/max/app/state"),
  set: (chatId: string, profile: string) =>
    request<{ ok: boolean; state: StateResponse }>("/max/app/set", {
      method: "POST",
      body: JSON.stringify({ chat_id: chatId, profile }),
    }),
};
