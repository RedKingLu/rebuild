/** Credential/BYOK Service — R6 credential management API client.
 *
 * Calls /api/credentials/* endpoints for listing, creating, rotating,
 * and deleting user-managed API keys (BYOK).
 */

import { get, post, del } from './client';

// ── Types ───────────────────────────────────────────────────────────

export interface CredentialInfo {
  credential_id: string;
  name: string;
  provider_ref?: string;
  key_source: string;
  key_fingerprint: string;
  masked_key: string;
  status: string;
  tenant_id: string;
  created_at: string;
  rotated_at?: string;
  source_status: string;
  capability_status: string;
}

export interface CredentialCreateRequest {
  name: string;
  plaintext_key: string;
  provider_ref?: string;
  key_source?: string;
  tenant_id?: string;
}

// ── API functions ────────────────────────────────────────────────────
// NOTE: the /credentials endpoints return *bare* shapes (not the {status,data,meta}
// envelope): GET → {credentials,total}; POST/rotate → CredentialResponse; DELETE → {success,detail}.

export async function listCredentials(): Promise<CredentialInfo[]> {
  const resp = await get<unknown>('/credentials');
  return ((resp as unknown as { credentials?: CredentialInfo[] }).credentials) || [];
}

export async function createCredential(data: CredentialCreateRequest): Promise<CredentialInfo> {
  const resp = await post<unknown>('/credentials', data);
  return resp as unknown as CredentialInfo;
}

export async function deleteCredential(id: string): Promise<void> {
  await del(`/credentials/${id}`);
}

export async function rotateCredential(id: string, new_key: string): Promise<CredentialInfo> {
  const resp = await post<unknown>(`/credentials/${id}/rotate`, { new_plaintext_key: new_key });
  return resp as unknown as CredentialInfo;
}
