/**
 * A project's vault: trees of branches and secrets.
 *
 * Nothing here returns a plaintext credential except `revealSecret`.
 */

import { jsonBody, request } from './http'
import type { Sensitivity } from './roles'

export type VaultNodeKind = 'branch' | 'secret'

export interface VaultTree {
  id: string
  project_id: string
  name: string
  position: number
  /** What nodes added here are classified as unless the caller says. */
  default_sensitivity: Sensitivity
  node_count: number
  secret_count: number
  created_at: string
  updated_at: string
}

export interface VaultTreeDetail extends VaultTree {
  nodes: VaultNode[]
}

/** A secret's metadata. There is deliberately no `value` here — only
 * `revealSecret` returns one, and only with the `vault:reveal` scope. */
export interface VaultSecretMeta {
  username: string | null
  url: string | null
  notes: string
  key_version: number
  updated_at: string
}

export interface VaultNode {
  id: string
  tree_id: string
  parent_id: string | null
  name: string
  kind: VaultNodeKind
  position: number
  /** How far it may travel. A branch above your clearance hides its subtree. */
  sensitivity: Sensitivity
  created_at: string
  updated_at: string
  secret: VaultSecretMeta | null
  children: VaultNode[]
}

export interface SecretInput {
  value?: string
  username?: string | null
  url?: string | null
  notes?: string
}

export interface VaultNodeInput {
  tree_id: string
  parent_id?: string | null
  name: string
  kind: VaultNodeKind
  secret?: SecretInput
  /** Null or omitted inherits the parent branch's, or the tree's default. */
  sensitivity?: Sensitivity | null
}

export interface RevealedSecret {
  node_id: string
  name: string
  value: string
  revealed_at: string
}

export const vaultApi = {
  listVaultTrees: (projectKey: string) =>
    request<VaultTree[]>(`/projects/${projectKey}/vault/trees`),
  createVaultTree: (projectKey: string, name: string, defaultSensitivity?: Sensitivity | null) =>
    request<VaultTree>(`/projects/${projectKey}/vault/trees`, {
      method: 'POST',
      body: jsonBody({ name, default_sensitivity: defaultSensitivity ?? null }),
    }),
  getVaultTree: (treeId: string) => request<VaultTreeDetail>(`/vault/trees/${treeId}`),

  createVaultNode: (input: VaultNodeInput) =>
    request<VaultNode>('/vault/nodes', { method: 'POST', body: jsonBody(input) }),
  updateVaultNode: (
    nodeId: string,
    input: { name?: string; secret?: SecretInput; sensitivity?: Sensitivity },
  ) => request<VaultNode>(`/vault/nodes/${nodeId}`, { method: 'PATCH', body: jsonBody(input) }),
  deleteVaultNode: (nodeId: string) =>
    request<{ ok: boolean }>(`/vault/nodes/${nodeId}`, { method: 'DELETE' }),
  moveVaultNode: (nodeId: string, parentId: string | null, position: number) =>
    request<VaultNode>(`/vault/nodes/${nodeId}/move`, {
      method: 'POST',
      body: jsonBody({ parent_id: parentId, position }),
    }),

  /**
   * Decrypt one stored secret.
   *
   * The only call in this client that returns a credential, and the only one
   * the server writes to the audit trail on sight. Never cache what it
   * returns beyond the moment it is shown.
   */
  revealSecret: (nodeId: string) =>
    request<RevealedSecret>(`/vault/nodes/${nodeId}/reveal`, { method: 'POST' }),
}
