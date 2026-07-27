@description('Container App name.')
param name string

param location string
param tags object = {}

param logAnalyticsCustomerId string

@secure()
param logAnalyticsSharedKey string

@description('Resource ID of the user-assigned managed identity.')
param identityId string

@description('Client ID of the user-assigned managed identity — passed through as AZURE_MANAGED_IDENTITY_CLIENT_ID so the app selects the right identity for Key Vault (see app/config.py).')
param identityClientId string

param acrLoginServer string
param image string
param keyVaultUrl string
param azureTenantId string
param azureClientId string
param llmModel string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${name}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        sharedKey: logAnalyticsSharedKey
      }
    }
  }
}

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'doctalk'
          image: image
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            { name: 'AUTH_ENABLED', value: 'true' }
            { name: 'AZURE_TENANT_ID', value: azureTenantId }
            { name: 'AZURE_CLIENT_ID', value: azureClientId }
            { name: 'AZURE_KEY_VAULT_URL', value: keyVaultUrl }
            { name: 'AZURE_MANAGED_IDENTITY_CLIENT_ID', value: identityClientId }
            { name: 'LLM_MODEL', value: llmModel }
            { name: 'LOG_FORMAT', value: 'json' }
          ]
          // No liveness/readiness probe override: Container Apps defaults to
          // probing the ingress target port, which is enough here — the app
          // has no separate lightweight health path beyond GET /health, and
          // that is unauthenticated (see app/main.py) precisely so a probe
          // can reach it.
        }
      ]
      scale: {
        // minReplicas: 1 keeps the embedding/reranker models warm (loaded at
        // startup — see app/main.py's lifespan hook); scaling to 0 trades
        // that for lower idle cost at the price of a slow first request
        // after an idle period.
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output fqdn string = containerApp.properties.configuration.ingress.fqdn
