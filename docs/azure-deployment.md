# Azure Deployment

How to deploy DocTalk to Azure Container Apps with the Bicep templates in
`infra/`.

> **Status: written, not deployed.** The templates are hand-authored against
> the Bicep/ARM resource schemas; CI (`.github/workflows/ci.yml`) runs
> `az bicep build` on every push, which is their only machine check. Nothing
> here has run against a live subscription, and the `pg_search` gap below
> blocks a clean deploy today. Run `az bicep build` and
> `az deployment group validate` before trusting any of it.

## Blocking gap: `pg_search` is not available on Azure Postgres

The lexical retrieval leg uses ParadeDB's `pg_search`
(`migrations/0001_initial_schema.sql`). Azure Database for PostgreSQL Flexible
Server allow-lists extensions and `pg_search` is not on the list, so
`CREATE EXTENSION pg_search` fails there. `infra/modules/postgres.bicep`
provisions the server with `pgvector` only and says so in a comment.

Resolve it one of two ways before deploying:

1. **`ts_rank`/`tsvector` fallback** for the lexical leg behind a config switch
   — weaker scorer, still hybrid. Named as the intended direction in
   [`technical-decisions.md`](technical-decisions.md#storage--migrations), not
   yet implemented.
2. **Run Postgres as a container** in the same environment — keeps `pg_search`,
   loses managed backups and patching.

Nothing else in this guide depends on which is chosen.

## Architecture

```
GitHub Actions (OIDC) ──push──▶ Azure Container Registry
                                        │
                                        ▼
User ──HTTPS──▶ Container Apps ──user-assigned identity──▶ Key Vault
                      │                              (database-url,
                      ▼                               openrouter-api-key)
              Azure Database for PostgreSQL Flexible Server
                     (pgvector; pg_search gap above)

Entra ID ──issues tokens──▶ User ──Authorization: Bearer──▶ Container Apps
   (validated in-process by app/api/auth.py, JWKS cached, no extra hop)
```

Same shape as `docker compose up`: one app container, one Postgres. Container
Apps replaces Compose, Key Vault + managed identity replace `.env`, Entra ID
replaces `AUTH_ENABLED=false`.

## Prerequisites

- An Azure subscription and the `az` CLI (`az bicep install`)
- Owner, or Contributor + User Access Administrator, on the target resource
  group — the template creates role assignments
- An OpenRouter API key
- Docker, to build the image the first time (or let CI do it)

## One-time setup

**1. Resource group**

```bash
az group create --name doctalk-dev --location eastus
```

**2. Entra ID app registration** — the audience DocTalk validates tokens
against (`AZURE_CLIENT_ID`/`azureClientId` throughout this repo).

```bash
az ad app create --display-name "DocTalk API" --sign-in-audience AzureADMyOrg
# Note the appId (client ID) and tenant ID (az account show --query tenantId)
```

Expose an API scope (portal: App registrations → DocTalk API → Expose an API →
Add a scope, e.g. `access_as_user`) so clients have something to request a
token for. `app/api/auth.py` checks `aud` (must equal the client ID) and `iss`
(must be this tenant) and nothing else — role/scope authorization is not
implemented, and is the next step for multi-tenant use.

**3. Federated credential for GitHub Actions** (optional, for CI/CD) — lets the
deploy workflow authenticate with OIDC instead of a stored secret.

```bash
az ad app create --display-name "doctalk-github-actions"
az ad sp create --id <appId>
az ad app federated-credential create --id <appId> --parameters '{
  "name": "doctalk-main-branch",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:<org>/<repo>:ref:refs/heads/main",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

This is a **separate identity** from step 2 — it deploys infrastructure, it
does not receive end-user tokens. Grant it Contributor + User Access
Administrator on the resource group.

## Deploying

```bash
az deployment group validate \
  --resource-group doctalk-dev \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters postgresAdminPassword='<generate one>' \
  --parameters openRouterApiKey='<your key>' \
  --parameters azureTenantId='<tenant id>' \
  --parameters azureClientId='<client id from step 2>'

# Same command with `create` instead of `validate` to deploy.
```

Secrets are passed on the command line (or a `--parameters @secrets.local.json`
kept out of git), never committed — `infra/main.parameters.json` holds
placeholders only.

**Build and push the image** (afterwards CI does this):

```bash
az acr login --name <acrName>   # registry name, i.e. acrLoginServer minus .azurecr.io
docker build -t <acrLoginServer>/doctalk:latest .
docker push <acrLoginServer>/doctalk:latest
```

**Apply the schema** to the new server — a manual step today, and blocked on
the `pg_search` question above. It runs `migrations/apply.sh`, the same runner
the local `migrate` service uses, so migrations stay tracked and checksummed
rather than being replayed by hand:

```bash
FQDN=$(az deployment group show -g doctalk-dev -n main \
  --query properties.outputs.postgresFqdn.value -o tsv)

# Azure Postgres requires TLS; the admin login and password are the ones
# passed to the deployment. URL-encode the password if it has reserved characters.
DATABASE_URL="postgresql://doctalkadmin:<password>@${FQDN}:5432/doctalk?sslmode=require" \
  bash migrations/apply.sh
```

## Secrets and identity

- **Key Vault** holds `database-url` and `openrouter-api-key`. `app/config.py`
  loads them via `DefaultAzureCredential` before `Settings` is constructed,
  keyed off `AZURE_KEY_VAULT_URL` — unset locally, so a local run never imports
  `azure-identity` or touches the network.
- **The Container App's user-assigned managed identity** gets `Key Vault
  Secrets User` (read-only) on the vault and `AcrPull` on the registry. No
  admin passwords, no client secrets stored anywhere.
- **`AZURE_CLIENT_ID` vs. `AZURE_MANAGED_IDENTITY_CLIENT_ID`** — the first is
  the app registration end-user tokens are validated against; the second is the
  managed identity's own client ID, passed explicitly to
  `DefaultAzureCredential(managed_identity_client_id=...)` so the two are never
  conflated.

## CI/CD

`.github/workflows/`, both illustrative — nothing runs them on a schedule:

- **`ci.yml`** — `az bicep build` on every push/PR. Lint and the test suite are
  run locally, not in CI.
- **`deploy.yml`** — manual (`workflow_dispatch`): build and push to ACR, then
  `validate` + `create` the deployment. Repository secrets required:
  `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_SUBSCRIPTION_ID` (the GitHub
  Actions identity from setup step 3 — **not** the app's AAD config),
  `ACR_NAME`, `AZURE_RESOURCE_GROUP`, `POSTGRES_ADMIN_PASSWORD`,
  `OPENROUTER_API_KEY`, `AAD_TENANT_ID`, `AAD_APP_CLIENT_ID` (step 2).

## Not implemented

| Gap | Impact |
|---|---|
| `ts_rank` lexical fallback | Blocks a real deployment, not a nice-to-have (see above) |
| Network isolation | Postgres uses the `AllowAzureServices` firewall rule, not VNet integration + private endpoint. Wrong for production data |
| Migration automation | Schema application is a manual `apply.sh` run, not a Container Apps Job triggered by deploy |
| Custom domain / WAF | Default `*.azurecontainerapps.io` ingress, as-is |
| Scope/role authorization | Token validation checks `aud`/`iss` only; every valid token can use the whole API — matches the single-workspace model |
| `/docs` stays open with `AUTH_ENABLED=true` | Registered on `app`, not on the routers `verify_token` guards. Exposes the API shape, not data; `docs_url=None` is a one-line fix |

## Teardown

```bash
az group delete --name doctalk-dev --yes --no-wait
```
