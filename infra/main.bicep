// ─────────────────────────────────────────────────────────────────────────────
// RAG Enterprise — Azure Infrastructure
// Provisions:
//   - Azure Container Registry (ACR)
//   - Azure Container Apps Environment (with Log Analytics)
//   - 3 Container Apps: main-agent, orchestrator-agent, retrieval-agent
//   - Azure AI Search (Standard tier for semantic ranking)
//   - Azure Service Bus (Standard tier with 2 queues)
//   - Application Insights
//   - Key Vault (for secrets at runtime)
// ─────────────────────────────────────────────────────────────────────────────

targetScope = 'resourceGroup'

@description('Short environment name, e.g. prod / staging / dev')
param environmentName string = 'prod'

@description('Azure region')
param location string = resourceGroup().location

@description('Azure OpenAI endpoint (already provisioned)')
param azureOpenAiEndpoint string

@description('Azure OpenAI chat deployment name')
param azureOpenAiChatDeployment string = 'gpt-4o'

@description('Azure OpenAI embedding deployment name')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-ada-002'

@description('Azure AI Search admin key (passed in securely)')
@secure()
param searchAdminKey string

@description('Teams App ID (optional)')
param teamsAppId string = ''

@description('Teams App Password (optional)')
@secure()
param teamsAppPassword string = ''

var prefix = 'rag-${environmentName}'
var tags = { environment: environmentName, project: 'rag-enterprise' }

// ── Log Analytics ─────────────────────────────────────────────────────────────
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ── Application Insights ──────────────────────────────────────────────────────
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-ai'
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ── Azure Container Registry ──────────────────────────────────────────────────
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${prefix}acr', '-', '')
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false  // use managed identity, not admin credentials
  }
}

// ── Azure AI Search ───────────────────────────────────────────────────────────
resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${prefix}-search'
  location: location
  tags: tags
  sku: { name: 'standard' }  // standard required for semantic ranker
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
  }
}

// ── Azure Service Bus ─────────────────────────────────────────────────────────
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${prefix}-sb'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
  properties: {}
}

resource sbQueueInbound 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'rag-inbound'
  properties: {
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'PT10M'
    lockDuration: 'PT2M'
    deadLetteringOnMessageExpiration: true
  }
}

resource sbQueueOutbound 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'rag-outbound'
  properties: {
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'PT10M'
    lockDuration: 'PT2M'
    deadLetteringOnMessageExpiration: true
  }
}

resource sbAuthRule 'Microsoft.ServiceBus/namespaces/AuthorizationRules@2022-10-01-preview' existing = {
  parent: serviceBusNamespace
  name: 'RootManageSharedAccessKey'
}

// ── Key Vault ─────────────────────────────────────────────────────────────────
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv'
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

// Store secrets in Key Vault
resource kvSecretSearchKey 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'AZURE-SEARCH-API-KEY'
  properties: { value: searchAdminKey }
}

resource kvSecretSbConnStr 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'AZURE-SERVICE-BUS-CONNECTION-STR'
  properties: { value: serviceBusNamespace.listKeys().primaryConnectionString }
}

resource kvSecretAppInsights 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'APPINSIGHTS-CONNECTION-STRING'
  properties: { value: appInsights.properties.ConnectionString }
}

// ── ACA Environment ───────────────────────────────────────────────────────────
resource acaEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-aca-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ── Managed Identity for Container Apps ──────────────────────────────────────
resource acaManagedId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-aca-id'
  location: location
  tags: tags
}

// ACR pull role
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, acaManagedId.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault secrets user role
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, acaManagedId.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Azure Cognitive Services user (for OpenAI token auth)
resource cogServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, acaManagedId.id, 'CognitiveServicesUser')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Common env vars shared across all Container Apps ──────────────────────────
var commonEnvVars = [
  { name: 'RUNNING_IN_AZURE', value: 'true' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAiEndpoint }
  { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: azureOpenAiChatDeployment }
  { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: azureOpenAiEmbeddingDeployment }
  { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
  { name: 'AZURE_SEARCH_API_KEY', secretRef: 'search-api-key' }
  { name: 'AZURE_SERVICE_BUS_CONNECTION_STR', secretRef: 'sb-conn-str' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-conn-str' }
  { name: 'CONFIDENCE_THRESHOLD', value: '0.75' }
  { name: 'MAX_RETRIEVAL_ATTEMPTS', value: '3' }
  { name: 'RETRIEVAL_TOP_K', value: '5' }
  { name: 'RETRIEVAL_MODE', value: 'servicebus' }
]

var commonSecrets = [
  {
    name: 'search-api-key'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/AZURE-SEARCH-API-KEY'
    identity: acaManagedId.id
  }
  {
    name: 'sb-conn-str'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/AZURE-SERVICE-BUS-CONNECTION-STR'
    identity: acaManagedId.id
  }
  {
    name: 'appinsights-conn-str'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/APPINSIGHTS-CONNECTION-STRING'
    identity: acaManagedId.id
  }
]

var acrImageBase = '${acr.properties.loginServer}'

// ── Retrieval Agent Container App ─────────────────────────────────────────────
resource retrievalApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-retrieval'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${acaManagedId.id}': {} }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: acaManagedId.id }]
      secrets: commonSecrets
      ingress: {
        external: false
        targetPort: 8002
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'retrieval-agent'
          image: '${acrImageBase}/rag-retrieval-agent:latest'
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: union(commonEnvVars, [
            { name: 'AGENT_PORT', value: '8002' }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8002 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

// ── Orchestrator Agent Container App ─────────────────────────────────────────
resource orchestratorApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-orchestrator'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${acaManagedId.id}': {} }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: acaManagedId.id }]
      secrets: commonSecrets
      ingress: {
        external: false
        targetPort: 8001
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'orchestrator-agent'
          image: '${acrImageBase}/rag-orchestrator-agent:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: union(commonEnvVars, [
            { name: 'AGENT_PORT', value: '8001' }
            { name: 'RETRIEVAL_AGENT_URL', value: 'https://${retrievalApp.properties.configuration.ingress.fqdn}' }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8001 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
  dependsOn: [retrievalApp]
}

// ── Main Agent Container App ──────────────────────────────────────────────────
resource mainApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-main'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${acaManagedId.id}': {} }
  }
  properties: {
    managedEnvironmentId: acaEnv.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: acaManagedId.id }]
      secrets: union(commonSecrets, [
        {
          name: 'teams-app-password'
          value: teamsAppPassword
        }
      ])
      ingress: {
        external: true   // publicly accessible for Teams Bot Service
        targetPort: 8000
        transport: 'http'
      }
    }
    template: {
      containers: [
        {
          name: 'main-agent'
          image: '${acrImageBase}/rag-main-agent:latest'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: union(commonEnvVars, [
            { name: 'AGENT_PORT', value: '8000' }
            { name: 'ORCHESTRATOR_AGENT_URL', value: 'https://${orchestratorApp.properties.configuration.ingress.fqdn}' }
            { name: 'TEAMS_APP_ID', value: teamsAppId }
            { name: 'TEAMS_APP_PASSWORD', secretRef: 'teams-app-password' }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
  dependsOn: [orchestratorApp]
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output acrLoginServer string = acr.properties.loginServer
output mainAgentFqdn string = mainApp.properties.configuration.ingress.fqdn
output orchestratorFqdn string = orchestratorApp.properties.configuration.ingress.fqdn
output retrievalFqdn string = retrievalApp.properties.configuration.ingress.fqdn
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output serviceBusNamespace string = serviceBusNamespace.name
output keyVaultUri string = keyVault.properties.vaultUri
output appInsightsConnectionString string = appInsights.properties.ConnectionString
