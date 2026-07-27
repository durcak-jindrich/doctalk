@description('PostgreSQL Flexible Server name (globally unique).')
param name string

param location string
param tags object = {}

param administratorLogin string

@secure()
param administratorPassword string

param databaseName string = 'doctalk'

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-06-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: {
      storageSizeGB: 32
    }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: {
      mode: 'Disabled'
    }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-06-01-preview' = {
  parent: postgres
  name: databaseName
}

// `pgvector` is on Azure's Flexible Server extension allow-list; ParadeDB's
// `pg_search` (the lexical/BM25 leg — see docs/technical-decisions.md) is
// NOT. Deploying migrations/0001_initial_schema.sql as-is against this
// server fails at `CREATE EXTENSION pg_search`. This is a known, documented
// gap — see docs/azure-deployment.md — not something this template papers
// over: only the dense-search extension is provisioned here today.
resource pgvectorExtension 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-06-01-preview' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR'
    source: 'user-override'
  }
}

// Lets Container Apps (which has no fixed outbound IP on the Consumption
// plan) reach the server. Tightening this to a VNet integration is a
// documented follow-up, not implemented here — see docs/azure-deployment.md.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-06-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

output fqdn string = postgres.properties.fullyQualifiedDomainName
output id string = postgres.id
