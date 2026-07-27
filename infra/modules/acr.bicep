@description('Azure Container Registry name (globally unique, alphanumeric only).')
param name string

param location string
param tags object = {}

@description('Principal ID of the identity that pulls images — the Container App\'s user-assigned managed identity.')
param principalId string

// Built-in role definition ID for "AcrPull" — lets the identity pull images
// without an admin username/password (admin user stays disabled below).
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

output loginServer string = acr.properties.loginServer
output id string = acr.id
