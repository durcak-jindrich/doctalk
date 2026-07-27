# Azure Deployment

How to deploy DocTalk to Azure Container Apps. The IaC (`infra/`) is
hand-authored against the Bicep/ARM resource schemas below **but has not
been run through `az bicep build`, `az deployment group validate`, or a
live subscription** — this dev environment has no Azure CLI installed. CI
(`.github/workflows/ci.yml`) runs `az bicep build` on every push, which is
the first real syntax/type check it gets; treat it as unverified until that
has gone green at least once. See
[Validating without deploying](#validating-without-deploying). This is
Phase 8 of `PLAN.md`; the assumptions here are also listed in `PLAN.md`'s
"Assumptions / open questions to flag to the Product Owner".

## Known gap: `pg_search` is not installable on Azure Database for PostgreSQL

DocTalk's lexical retrieval leg uses ParadeDB's `pg_search` extension
(`migrations/0001_initial_schema.sql`). Azure Database for PostgreSQL
Flexible Server allow-lists extensions, and `pg_search` is not on that list —
`CREATE EXTENSION pg_search` fails there. `infra/modules/postgres.bicep`
provisions the server with only `pgvector` enabled and says so in a comment;
it does not pretend the current schema deploys clean.

**Before a real Azure deployment**, either:
1. Add a `ts_rank`/`tsvector` fallback for the lexical leg behind a config
   switch (the fallback `docs/technical-decisions.md` already names as the
   intended direction, not yet implemented), or
2. Run Postgres as a container inside the same environment instead of Azure
   Database for PostgreSQL (loses managed-service backups/patching).

Everything else in this guide — auth, Key Vault, Container Apps, CI/CD —
does not depend on which of those two is chosen.

## Architecture on Azure

```
GitHub Actions (OIDC) ──push──▶ Azure Container Registry
                                        │
                                        ▼
User ──HTTPS──▶ Container Apps ──user-assigned identity──▶ Key Vault (secrets)
                      │                                          │
                      │                                    (database-url,
                      ▼                                     openrouter-api-key)
              Azure Database for
              PostgreSQL Flexible Server
                (pgvector; pg_search gap above)

Entra ID ──issues tokens──▶ User ──Authorization: Bearer──▶ Container Apps
  (validated in-process by app/api/auth.py, JWKS cached, no extra hop)
```

Same shape as `docker compose up` locally: one app container, one Postgres.
Container Apps replaces Compose; Key Vault + Managed Identity replace the
`.env` file; Entra ID replaces `AUTH_ENABLED=false`.

## Prerequisites

- An Azure subscription and `az` CLI (`az bicep install` for the Bicep tooling)
- Owner or Contributor + User Access Administrator on the target resource
  group (the template creates role assignments)
- An OpenRouter API key
- Docker, to build the image the first time (or let CI do it — see
  [CI/CD](#cicd))

## One-time setup

**1. Resource group**
```bash
az group create --name doctalk-dev --location eastus
```

**2. Entra ID app registration** — this is the audience DocTalk validates
tokens against (`AZURE_CLIENT_ID`/`azureClientId` throughout this repo).
```bash
az ad app create --display-name "DocTalk API" --sign-in-audience AzureADMyOrg
# Note the appId (client ID) and your tenant ID (az account show --query tenantId)
```
Expose an API scope (Entra ID portal: App registrations → DocTalk API →
Expose an API → Add a scope, e.g. `access_as_user`) so client applications
have something to request a token for. Any user or service calling
`/api/*` needs an access token issued by this app registration —
`app/api/auth.py` checks `aud` (must equal the client ID) and `iss` (must be
this tenant), nothing more; role/scope-based authorization is not
implemented and would be the next step for multi-tenant use.

**3. (Optional, for CI/CD) Federated credential for GitHub Actions** — lets
the deploy workflow authenticate with OIDC instead of a stored client
secret:
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
This app registration is a **separate identity** from the one in step 2 —
it deploys infrastructure, it does not receive end-user tokens. Grant it
Contributor + User Access Administrator on the resource group.

## Deploying

```bash
az deployment group validate \
  --resource-group doctalk-dev \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters postgresAdminPassword='<generate one>' \
  --parameters openRouterApiKey='<your key>' \
  --parameters azureTenantId='<tenant id>' \
  --parameters azureClientId='<app registration client id from step 2>'

# Same command with `create` instead of `validate` to actually deploy.
```

Secrets are passed as `--parameters` on the command line (or via a
`--parameters @secrets.local.json` file kept out of git), never committed —
`infra/main.parameters.json` only holds placeholder values.

**After the first deploy**, apply the schema against the new server (see the
`pg_search` gap above — this step needs the extension question resolved
first):
```bash
psql "$(az deployment group show -g doctalk-dev -n main --query properties.outputs.postgresFqdn.value -o tsv)" \
  -f migrations/0001_initial_schema.sql
```
This is a manual step today, mirroring the local `migrate` Compose service
(`migrations/apply.sh`) — turning it into an Azure Container Apps Job is a
documented follow-up, not yet built.

**Build and push the image** the first time (afterwards, CI does this — see
below):
```bash
az acr login --name <acrLoginServer from the deployment output, minus .azurecr.io>
docker build -t <acrLoginServer>/doctalk:latest .
docker push <acrLoginServer>/doctalk:latest
```

## Validating without deploying

`az bicep build --file infra/main.bicep` catches syntax and type errors with
no Azure credentials needed — it runs in CI (`.github/workflows/ci.yml`) on
every push, which is the cheapest real check available. `az deployment group
validate` goes further, checking the template against a real resource group
and real parameter values without creating anything. Neither has been run
against a real subscription yet — this repo was built in an environment
without the Azure CLI installed, so the template is reviewed against the
Bicep/ARM resource schemas, not machine-checked. Run `az bicep build` before
trusting it for a real deployment.

## CI/CD

Two example workflows in `.github/workflows/`:
- **`ci.yml`** — lint + test (fake-LLM suite, no OpenRouter quota spent) +
  `az bicep build`, on every push/PR.
- **`deploy.yml`** — manual (`workflow_dispatch`), builds and pushes the
  image to ACR, then `validate`s and `create`s the Bicep deployment. Needs
  these repository secrets: `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/
  `AZURE_SUBSCRIPTION_ID` (the GitHub Actions federated identity from setup
  step 3 — **not** the app's own AAD config), `ACR_NAME`,
  `AZURE_RESOURCE_GROUP`, `POSTGRES_ADMIN_PASSWORD`, `OPENROUTER_API_KEY`,
  `AAD_TENANT_ID`, `AAD_APP_CLIENT_ID` (the app registration from setup
  step 2).

Both are illustrative — no environment currently runs them on a schedule.

## Secrets and identity

- **Key Vault** holds two secrets: `database-url`, `openrouter-api-key`.
  `app/config.py` loads them via `DefaultAzureCredential` before `Settings`
  is constructed, keyed off `AZURE_KEY_VAULT_URL`; unset locally, so a local
  run never imports `azure-identity` or touches the network.
- **The Container App's user-assigned managed identity** is granted
  `Key Vault Secrets User` (read-only) on the vault and `AcrPull` on the
  registry — no admin passwords, no client secrets stored anywhere.
- **`AZURE_CLIENT_ID` vs. `AZURE_MANAGED_IDENTITY_CLIENT_ID`**: the former is
  the Entra ID app registration DocTalk validates end-user tokens against;
  the latter is the managed identity's own client ID, passed explicitly to
  `DefaultAzureCredential(managed_identity_client_id=...)` so the two are
  never conflated (see the comment in `app/config.py`).

## What's not implemented

- **`ts_rank` lexical fallback** — see the gap above; the blocker for a real
  deployment, not just a nice-to-have.
- **Network isolation** — Postgres allows all Azure-origin IPs
  (`AllowAzureServices` firewall rule) rather than VNet-integrating Container
  Apps and using a private endpoint. Fine for a case-study deployment,
  wrong for production data.
- **Migration automation** — schema application is a manual `psql` step
  (above), not an Azure Container Apps Job triggered by deploy.
- **Custom domain / WAF** — Container Apps' default `*.azurecontainerapps.io`
  ingress is used as-is.
- **Scope/role-based authorization** — token validation checks `aud`/`iss`
  only (see setup step 2); every valid token can use the whole API. Matches
  the single shared-workspace model the rest of DocTalk assumes (see
  README's Limitations section) — finer-grained authorization has no
  per-user data to scope to yet.
- **`/docs` and `/openapi.json` stay unauthenticated even with
  `AUTH_ENABLED=true`** — they're registered directly on `app`, not through
  the `documents`/`ask` routers `verify_token` is attached to (`app/main.py`).
  Exposes the API shape, not data; disabling them in Azure (FastAPI's
  `docs_url=None`) is a one-line follow-up if that's unwanted.

## Teardown

```bash
az group delete --name doctalk-dev --yes --no-wait
```
