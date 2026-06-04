// ─────────────────────────────────────────────────────────────────────────────
// RAG Enterprise — Azure Infrastructure (Retrieval Pipeline)
// ─────────────────────────────────────────────────────────────────────────────

targetScope = 'resourceGroup'

@description('Short environment name: prod / staging / dev')
param environmentName string = 'prod'

param location string = resourceGroup().location

@description('Azure AI Foundry project endpoint')
param azureFoundryProjectEndpoint string

@description('Azure OpenAI chat deployment name')
param azureOpenAiChatDeployment string = 'gpt-4o'

@description('Azure OpenAI embedding deployment name')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-ada-002'

@description('Azure Cosmos DB account endpoint (create separately or deploy here)')
param azureCosmosEndpoint string

@description('Entra ID tenant ID (for JWT validation)')
param azureTenantId string

@description('App registration client ID (audience for JWT validation)')
param azureClientId string

@description('Teams App ID (optional)')
param teamsAppId string = ''

@secure()
@description('Teams App Password (optional)')
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
    adminUserEnabled: false
  }
}

// ── Azure AI Search (Standard — required for semantic ranker) ─────────────────
resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: '${prefix}-search'
  location: location
  tags: tags
  sku: { name: 'standard' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
    authOptions: {
      // Enable both key and RBAC — retrieval agents use RBAC (keyless)
      aadOrApiKey: { aadAuthFailureMode: 'http401WithBearerChallenge' }
    }
  }
}

// ── Azure Service Bus (Standard — sessions require Standard or Premium) ────────
// NOTE: rag-inbound and rag-outbound MUST have requiresSession: true
// This is what eliminates the multi-request race condition.
resource serviceBusNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${prefix}-sb'
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard' }
}

resource sbQueueInbound 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'rag-inbound'
  properties: {
    requiresSession: true               // ← REQUIRED for session-based correlation
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
    requiresSession: true               // ← REQUIRED for session-based correlation
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'PT10M'
    lockDuration: 'PT2M'
    deadLetteringOnMessageExpiration: true
  }
}

resource sbQueueEvaluation 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: serviceBusNamespace
  name: 'rag-evaluation'
  properties: {
    requiresSession: false
    maxDeliveryCount: 3
    defaultMessageTimeToLive: 'PT1H'
    lockDuration: 'PT5M'
    deadLetteringOnMessageExpiration: true
  }
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

// ── Managed Identity ──────────────────────────────────────────────────────────
resource acaManagedId 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-aca-id'
  location: location
  tags: tags
}

// ACR Pull
resource acrPullRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, acaManagedId.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Secrets User
resource kvSecretsUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, acaManagedId.id, 'KeyVaultSecretsUser')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services OpenAI User (Foundry inference)
resource cogServicesRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, acaManagedId.id, 'CognitiveServicesUser')
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Azure AI Search Index Data Reader (keyless retrieval)
resource searchDataReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, acaManagedId.id, 'SearchIndexDataReader')
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '1407120a-92aa-4202-b7e9-c0e197c71c8f')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Service Bus Data Owner (send + receive on all queues)
resource sbDataOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespace.id, acaManagedId.id, 'SBDataOwner')
  scope: serviceBusNamespace
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '090c5cfd-751d-490a-894a-3ce6f1109419')
    principalId: acaManagedId.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// NOTE: CosmosDB RBAC must be assigned separately via az CLI (not ARM/Bicep):
//   az cosmosdb sql role assignment create \
//     --account-name <cosmos-account> --resource-group <rg> \
//     --role-definition-name "Cosmos DB Built-in Data Contributor" \
//     --principal-id <acaManagedId.properties.principalId> \
//     --scope /subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.DocumentDB/databaseAccounts/<account>

// ── Secrets ───────────────────────────────────────────────────────────────────
var commonSecrets = [
  {
    name: 'appinsights-conn-str'
    keyVaultUrl: '${keyVault.properties.vaultUri}secrets/APPINSIGHTS-CONNECTION-STRING'
    identity: acaManagedId.id
  }
  // Teams password stored inline (not KV) — acceptable for optional bot config
  {
    name: 'teams-app-password'
    value: teamsAppPassword
  }
]

// ── Common env vars ───────────────────────────────────────────────────────────
var commonEnvVars = [
  { name: 'RUNNING_IN_AZURE', value: 'true' }
  { name: 'LOG_LEVEL', value: 'INFO' }
  { name: 'AZURE_FOUNDRY_PROJECT_ENDPOINT', value: azureFoundryProjectEndpoint }
  { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: azureOpenAiChatDeployment }
  { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: azureOpenAiEmbeddingDeployment }
  { name: 'AZURE_OPENAI_API_VERSION', value: '2024-08-01-preview' }
  { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
  { name: 'AZURE_SEARCH_INDEX', value: 'idx-rag' }
  { name: 'AZURE_SEARCH_SEMANTIC_CONFIG', value: 'rag-semantic-config' }
  { name: 'AZURE_SERVICE_BUS_NAMESPACE', value: '${serviceBusNamespace.name}.servicebus.windows.net' }
  { name: 'AZURE_COSMOS_ENDPOINT', value: azureCosmosEndpoint }
  { name: 'AZURE_TENANT_ID', value: azureTenantId }
  { name: 'AZURE_CLIENT_ID', value: azureClientId }
  { name: 'CONFIDENCE_THRESHOLD', value: '0.75' }
  { name: 'MAX_RETRIEVAL_ATTEMPTS', value: '3' }
  { name: 'RETRIEVAL_TOP_K', value: '5' }
  { name: 'SYNTHESIS_TEMPERATURE', value: '0.0' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-conn-str' }
]

var acrImageBase = acr.properties.loginServer

// ── Retrieval Agent ───────────────────────────────────────────────────────────
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
      ingress: { external: false, targetPort: 8002, transport: 'http' }
    }
    template: {
      containers: [{
        name: 'retrieval-agent'
        image: '${acrImageBase}/rag-retrieval-agent:latest'
        resources: { cpu: json('1.0'), memory: '2Gi' }
        env: union(commonEnvVars, [{ name: 'AGENT_PORT', value: '8002' }])
        probes: [{
          type: 'Liveness'
          httpGet: { path: '/health', port: 8002 }
          initialDelaySeconds: 15
          periodSeconds: 30
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
}

// ── Orchestrator Agent ────────────────────────────────────────────────────────
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
      ingress: { external: false, targetPort: 8001, transport: 'http' }
    }
    template: {
      containers: [{
        name: 'orchestrator-agent'
        image: '${acrImageBase}/rag-orchestrator-agent:latest'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: union(commonEnvVars, [{ name: 'AGENT_PORT', value: '8001' }])
        probes: [{
          type: 'Liveness'
          httpGet: { path: '/health', port: 8001 }
          initialDelaySeconds: 15
          periodSeconds: 30
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
  dependsOn: [retrievalApp]
}

// ── Main Agent ────────────────────────────────────────────────────────────────
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
      secrets: commonSecrets
      ingress: { external: true, targetPort: 8000, transport: 'http' }
    }
    template: {
      containers: [{
        name: 'main-agent'
        image: '${acrImageBase}/rag-main-agent:latest'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: union(commonEnvVars, [
          { name: 'AGENT_PORT', value: '8000' }
          { name: 'ORCHESTRATOR_AGENT_URL', value: 'https://${orchestratorApp.properties.configuration.ingress.fqdn}' }
          { name: 'TEAMS_APP_ID', value: teamsAppId }
          { name: 'TEAMS_APP_PASSWORD', secretRef: 'teams-app-password' }
        ])
        probes: [{
          type: 'Liveness'
          httpGet: { path: '/health', port: 8000 }
          initialDelaySeconds: 15
          periodSeconds: 30
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 5 }
    }
  }
  dependsOn: [orchestratorApp]
}

// ── Evaluation Agent ──────────────────────────────────────────────────────────
resource evaluationApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-evaluation'
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
      // No external ingress — SB listener only
      ingress: { external: false, targetPort: 8003, transport: 'http' }
    }
    template: {
      containers: [{
        name: 'evaluation-agent'
        image: '${acrImageBase}/rag-evaluation-agent:latest'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: union(commonEnvVars, [
          { name: 'AGENT_PORT', value: '8003' }
          { name: 'AZURE_OPENAI_EVAL_DEPLOYMENT', value: 'gpt-4o-mini' }
        ])
        probes: [{
          type: 'Liveness'
          httpGet: { path: '/health', port: 8003 }
          initialDelaySeconds: 15
          periodSeconds: 30
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// ── Feedback Agent ────────────────────────────────────────────────────────────
resource feedbackApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-feedback'
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
      ingress: { external: false, targetPort: 8004, transport: 'http' }
    }
    template: {
      containers: [{
        name: 'feedback-agent'
        image: '${acrImageBase}/rag-feedback-agent:latest'
        resources: { cpu: json('0.25'), memory: '0.5Gi' }
        env: union(commonEnvVars, [{ name: 'AGENT_PORT', value: '8004' }])
        probes: [{
          type: 'Liveness'
          httpGet: { path: '/health', port: 8004 }
          initialDelaySeconds: 15
          periodSeconds: 30
        }]
      }]
      scale: { minReplicas: 1, maxReplicas: 2 }
    }
  }
}

// ── Outputs ───────────────────────────────────────────────────────────────────
output acrLoginServer string = acr.properties.loginServer
output mainAgentFqdn string = mainApp.properties.configuration.ingress.fqdn
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output serviceBusNamespace string = '${serviceBusNamespace.name}.servicebus.windows.net'
output keyVaultUri string = keyVault.properties.vaultUri
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output managedIdentityPrincipalId string = acaManagedId.properties.principalId
