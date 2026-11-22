# Cylist Helm chart (CYLIST-192)

`cylist/` is one chart that is installed twice per namespace. The `component`
value picks the half:

| Release | `component` | Holds |
|---|---|---|
| `cylist-data` | `data` | Postgres 16 StatefulSet with a postgres_exporter sidecar on `:9187`, the blobs volume, and the nightly backup CronJob with its volume |
| `cylist` (prod, staging), `cylist-N` (dev slot N) | `app` | Deployment, Service, Ingress, ConfigMap, and the `alembic upgrade head` pre-install/pre-upgrade Job |

The data and app halves are separate releases for two reasons. The migration
hook always finds its database already running, even on a first install. And
`helm rollback` of an app never touches Postgres or the blobs. The dump that
the deploy takes before migrating is the only way to undo a migration.

## Values

- `values.yaml` holds the defaults shared by every environment.
- `values-prod.yaml` and `values-staging.yaml` set each environment's hostname,
  `CYLIST_ENVIRONMENT`, client URLs, priority class and sizes.
- `values-dev.yaml` applies to dev's data release and to every slot.
  `values-dev-N.yaml` adds slot N's hostname and client URLs, plus the
  optional `cylist-slot-N-db` secret. A slot whose branch has migrations that
  main lacks gets its own database through that secret.

Every app gets `CYLIST_TRUSTED_PROXY_CIDRS=["10.42.0.0/16"]`. Without it, the
sign-in log and the rate limiter would record Traefik's pod IP instead of the
client's.

## Secrets

The chart never creates secrets. `deploy/k3s/secrets.sh prod|staging|dev`
builds `cylist-app`, `cylist-postgres` and (prod only) `cylist-fcm` from the
`app.env`, `postgres.env` and `fcm-service-account.json` files that the
Docker stacks read.

## Install

```sh
C=deploy/helm/cylist
deploy/k3s/secrets.sh dev
helm upgrade --install cylist-data $C -n cylist-dev -f $C/values-dev.yaml --set component=data
kubectl -n cylist-dev rollout status statefulset/cylist-data-postgres
helm upgrade --install cylist-1 $C -n cylist-dev -f $C/values-dev.yaml -f $C/values-dev-1.yaml \
  --set component=app --set image.tag=<tag> --rollback-on-failure --wait
```

Install the data release without `--wait`. local-path binds a volume only once
a pod uses it, so `--wait` would never see the blobs or backups volume Bound.

## Checks

- `deploy/helm/lint.sh` runs `helm lint` on every release, and also renders
  each one and validates the result with kubeconform.
- `deploy/helm/smoke-test.sh <image>` creates a throwaway k3d cluster with the
  same platform manifests. It installs dev's data release and slot 1, and
  checks `/api/v1/health` through Traefik. It then upgrades the slot and runs
  one backup.
- CI's "Helm chart" job runs both, using an image built from the commit.

## Probes

- **Readiness:** `/api/v1/health`, which asks the database. A pod whose
  database is down stops receiving traffic.
- **Startup and liveness:** `/api/v1/health/requests`, which reads in-memory
  counters only, so a database outage does not also restart the app.

## Known behaviour

- **New pods are refused for a few seconds.** K3s's network policy controller
  adds a new pod's address to its allow lists a few seconds after the pod
  starts, and until then Postgres refuses the connection. The backup Job waits
  for `pg_isready`, and the migration Job retries on a refused connection.
