// DocTalk — Azure Container Apps deployment.
//
// Resource-group scoped: `az group create` first, then
// `az deployment group create -g <rg> -f infra/main.bicep -p infra/main.parameters.json`.
// See docs/azure-deployment.md for the full walkthrough, prerequisites, and
// the known pg_search-on-Azure gap (modules/postgres.bicep).
targetScope = 'resourceGroup'

@description('Short prefix for every resource name (lowercase alphanumeric).')
@minLength(3)
@maxLength(12)
param namePrefix string = 'doctalk'

param location string = resourceGroup().location

@allowed(['dev', 'staging', 'prod'])
param environmentName string = 'dev'

@description('PostgreSQL Flexible Server administrator login.')
param postgresAdminLogin string = 'doctalkadmin'

@description('PostgreSQL Flexible Server administrator password. Pass via --parameters at deploy time, never commit a value.')
@secure()
param postgresAdminPassword string

@description('OpenRouter API key, stored in Key Vault. Pass via --parameters at deploy time, never commit a value.')
@secure()
param openRouterApiKey string

@description('Entra ID tenant ID the API validates access tokens against.')
param azureTenantId string

@description('Entra ID app registration (client) ID — the API\'s expected token audience. See docs/azure-deployment.md for how to register it.')
param azureClientId string

@description('Image tag to deploy — pushed to the ACR this template creates, e.g. by the example GitHub Actions workflow.')
param imageTag string = 'latest'

@description('LLM model slug passed to OpenRouter.')
param llmModel string = 'google/gemma-4-31b-it:free'

var resourceToken = uniqueString(subscription().id, resourceGroup().id, namePrefix)
var tags = {
  application: 'doctalk'
  environment: environmentName
}

module logAnalytics 'modules/log-analytics.bicep' = {
  name: 'log-analytics'
  params: {
    name: '${namePrefix}-log-${resourceToken}'
    location: location
    tags: tags
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    name: '${namePrefix}-id-${resourceToken}'
    location: location
    tags: tags
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    name: '${namePrefix}acr${resourceToken}'
    location: location
    tags: tags
    principalId: identity.outputs.principalId
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    name: '${namePrefix}-pg-${resourceToken}'
    location: location
    tags: tags
    administratorLogin: postgresAdminLogin
    administratorPassword: postgresAdminPassword
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    name: '${namePrefix}-kv-${resourceToken}'
    location: location
    tags: tags
    principalId: identity.outputs.principalId
    openRouterApiKey: openRouterApiKey
    databaseUrl: 'postgresql://${postgresAdminLogin}:${postgresAdminPassword}@${postgres.outputs.fqdn}:5432/doctalk?sslmode=require'
  }
}

module containerApp 'modules/container-app.bicep' = {
  name: 'container-app'
  params: {
    name: '${namePrefix}-app-${resourceToken}'
    location: location
    tags: tags
    logAnalyticsCustomerId: logAnalytics.outputs.customerId
    logAnalyticsSharedKey: logAnalytics.outputs.primarySharedKey
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    acrLoginServer: acr.outputs.loginServer
    image: '${acr.outputs.loginServer}/doctalk:${imageTag}'
    keyVaultUrl: keyVault.outputs.vaultUri
    azureTenantId: azureTenantId
    azureClientId: azureClientId
    llmModel: llmModel
  }
}

output containerAppUrl string = 'https://${containerApp.outputs.fqdn}'
output acrLoginServer string = acr.outputs.loginServer
output keyVaultUri string = keyVault.outputs.vaultUri
output postgresFqdn string = postgres.outputs.fqdn
output managedIdentityId string = identity.outputs.id
