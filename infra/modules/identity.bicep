@description('User-assigned managed identity name. Used by the Container App to pull from ACR and read Key Vault secrets — no admin passwords or connection strings stored in app settings.')
param name string

param location string
param tags object = {}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output id string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
