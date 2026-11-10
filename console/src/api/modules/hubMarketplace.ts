import { getApiToken, getApiUrl } from "../config";
import { responseErrorMessage } from "../error";

export type MarketplaceStatus = "pending" | "published" | "rejected";

export interface MarketplaceItem {
  id: string;
  name: string;
  display_name: string;
  version: string;
  description: string;
  emoji: string;
  tags: string[];
  status: MarketplaceStatus;
  submitter_user_id: string;
  submitter_username: string;
  source_filename: string;
  sha256: string;
  scan_passed: boolean;
  scan_report: string;
  review_note: string;
  reviewed_by: string;
  reviewed_at: string;
  published_at: string;
  created_at: string;
  updated_at: string;
}

async function marketplaceRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(getApiUrl(`/hub/marketplace${path}`), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${getApiToken()}`,
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(
      await responseErrorMessage(response, "Marketplace request failed"),
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const hubMarketplaceApi = {
  list: (params: { status?: MarketplaceStatus; query?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.query?.trim()) query.set("q", params.query.trim());
    const suffix = query.toString();
    return marketplaceRequest<{ items: MarketplaceItem[] }>(
      `/items${suffix ? `?${suffix}` : ""}`,
    );
  },

  get: (itemId: string) =>
    marketplaceRequest<MarketplaceItem>(`/items/${itemId}`),

  upload: async (files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const response = await fetch(
      getApiUrl("/hub/marketplace/items"),
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${getApiToken()}`,
        },
        body: form,
      },
    );
    if (!response.ok) {
      throw new Error(
        await responseErrorMessage(response, "Skill upload failed"),
      );
    }
    return response.json() as Promise<{ items: MarketplaceItem[] }>;
  },

  review: (ids: string[], action: "approve" | "reject", note = "") =>
    marketplaceRequest<{ items: MarketplaceItem[] }>("/review", {
      method: "POST",
      body: JSON.stringify({ ids, action, note }),
    }),

  install: (itemId: string) =>
    marketplaceRequest<{
      installed: boolean;
      runtime_id: string;
      result: Record<string, unknown>;
    }>(`/items/${itemId}/install`, { method: "POST" }),

  broadcast: (itemId: string) =>
    marketplaceRequest<{
      installed: { user_id: string; username: string; runtime_id: string }[];
      failed: { user_id: string; username: string; error: string }[];
    }>(`/items/${itemId}/broadcast`, { method: "POST" }),

  download: async (item: MarketplaceItem) => {
    const response = await fetch(
      getApiUrl(`/hub/marketplace/items/${item.id}/download`),
      {
        headers: {
          Authorization: `Bearer ${getApiToken()}`,
        },
      },
    );
    if (!response.ok) {
      throw new Error(
        await responseErrorMessage(response, "Skill download failed"),
      );
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${item.name}.zip`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },
};
