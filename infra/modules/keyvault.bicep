@description('Key Vault name (globally unique).')
param name string

param location string
param tags object = {}

@description('Principal ID of the identity that reads secrets — the Container App\'s user-assigned managed identity.')
param principalId string

@secure()
param openRouterApiKey string

@secure()
param databaseUrl string

// Built-in role definition ID for "Key Vault Secrets User" — read-only get/list
// on secrets, nothing else. RBAC authorization (not vault access policies) is
// used so this is one assignment, consistent with the rest of the identity's
// role assignments (see modules/acr.bicep).
var secretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Secret names use hyphens (Key Vault disallows underscores); app/config.py's
// _KEY_VAULT_SECRET_ENV_MAP maps each one back to its env var.
resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'database-url'
  properties: {
    value: databaseUrl
  }
}

resource openRouterSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'openrouter-api-key'
  properties: {
    value: openRouterApiKey
  }
}

resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, secretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', secretsUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output vaultUri string = keyVault.properties.vaultUri
output id string = keyVault.id
