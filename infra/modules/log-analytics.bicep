@description('Log Analytics workspace name.')
param name string

param location string
param tags object = {}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

output customerId string = logAnalytics.properties.customerId
@secure()
output primarySharedKey string = logAnalytics.listKeys().primarySharedKey
output id string = logAnalytics.id
