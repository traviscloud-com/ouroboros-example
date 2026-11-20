# deploy

Kubernetes manifests for Ouroboros (plain Kustomize, no Argo CD or Flux resources).

```
deploy/apps/ouroboros/
├── base/                  api + reporting Deployments/Services, Postgres cluster, ACR pull ExternalSecret, JWKS ConfigMap, NetworkPolicy, namespace
├── routes/                HTTPRoutes and the route-attached SecurityPolicies (login, allow-list, own-auth)
└── overlays/rancherlocal/ per-environment config: hostnames, registry and image tags, allow-list, JWKS, reporting env (trusted proxy CIDR, admins, public URL), env label
```

The platform's Argo CD on rancherlocal syncs `deploy/apps/ouroboros/overlays/rancherlocal`
(one application covering both the api and reporting workloads). The overlay includes `base` and `routes`.

Secrets are never committed. The registry pull credential is an ExternalSecret that reads Key Vault
through the platform's `azure-kv` ClusterSecretStore; other secrets are referenced by name only.
The JWKS in the overlay is a public key.

Render locally:

```
kustomize build deploy/apps/ouroboros/overlays/rancherlocal
```
