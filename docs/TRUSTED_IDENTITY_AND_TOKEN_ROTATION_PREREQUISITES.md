# Trusted Identity and Service Token Prerequisites

## Current safe state

`TRUSTED_IDENTITY_HEADERS=false`. Privileged Agentic routes reject unauthenticated requests. If enabled, headers are accepted only when the source address is in `TRUSTED_PROXY_CIDRS` and `X-Trusted-Proxy-Secret` matches `TRUSTED_PROXY_SHARED_SECRET` in constant time. Signed JWT mode validates the configured issuer, audience, algorithm, signature, expiry, issued-at and subject, with optional required ACR.

## Required OAuth/JWT contract

Before enabling trusted identity, provide one of these reviewed boundaries:

1. An OpenShift OAuth proxy sidecar that removes inbound identity headers, authenticates the caller, and injects `X-Forwarded-User`, `X-Forwarded-Groups`, and an authentication-strength claim; or
2. Direct JWT validation in the application with pinned issuer, audience, JWKS trust, expiry, subject, group/role mapping, and MFA/authentication-context validation.

The Route must expose only the proxy, not the application container. The application must accept proxy headers only from the local proxy connection or a documented trusted proxy CIDR. Add negative tests for spoofed headers, invalid issuer/audience, expired tokens, unsigned tokens, and missing MFA claims.

Current namespace inspection found no OAuth proxy container or OAuth-annotated Route. Current permissions cannot read cluster-scoped OAuth configuration. Platform/OCP identity-owner confirmation is required.

## pg_profile service-token rotation

The existing value has been moved from a literal Deployment environment value to Secret `object-monitor-pgprofile-service-auth`, key `PGPROFILE_SERVICE_TOKEN`.

Do not rotate until external consumers are inventoried. Namespace workload discovery found no other consumer, but external callers cannot be excluded with current visibility.

Recommended no-outage rotation:

1. Application support for `AGENTIC_SERVICE_TOKEN` and `AGENTIC_SERVICE_TOKEN_NEXT` is implemented using constant-time comparison.
2. Generate the next token through the approved secret-management system.
3. Distribute only the next token to known consumers and verify access/audit evidence.
4. Remove acceptance of the old token after the migration window.
5. Rotate the Secret again so only one current token remains.
6. Confirm Deployment manifests, logs, evidence, and backups do not contain decoded token values.

Keep all Agentic execution flags disabled throughout identity and token rollout.
