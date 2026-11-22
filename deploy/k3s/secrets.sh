#!/usr/bin/env bash
# Make an environment's Kubernetes Secrets from the files its Docker stack
# already reads, so the two never disagree and nothing secret is committed.
#
#   deploy/k3s/secrets.sh prod|staging|dev
#
#   source dir           namespace        secrets
#   ~/cylist-prod        cylist-prod      cylist-app, cylist-postgres, cylist-fcm
#   ~/cylist-staging     cylist-staging   cylist-app, cylist-postgres
#   ~/cylist-dev-shared  cylist-dev       cylist-app, cylist-postgres
#
# The directories can be moved with CYLIST_PROD_DIR, CYLIST_STAGING_DIR and
# CYLIST_DEV_DIR, as scripts/deploy.sh reads them. Re-running replaces the
# Secrets with what the files hold now; running pods pick changes up on their
# next restart.
#
# Two values are changed on the way in:
#   - CYLIST_DATABASE_URL's host becomes the data release's Postgres service,
#     since the compose host name means nothing in the cluster.
#   - CYLIST_FCM_SERVICE_ACCOUNT_FILE is dropped: the chart mounts cylist-fcm
#     and sets the path itself.
set -euo pipefail

# The chart's dataRelease, and so its Postgres service, in every namespace.
POSTGRES_HOST="cylist-data-postgres:5432"

case "${1:-}" in
  prod) dir="${CYLIST_PROD_DIR:-$HOME/cylist-prod}" ns=cylist-prod ;;
  staging) dir="${CYLIST_STAGING_DIR:-$HOME/cylist-staging}" ns=cylist-staging ;;
  dev) dir="${CYLIST_DEV_DIR:-$HOME/cylist-dev-shared}" ns=cylist-dev ;;
  *)
    echo "usage: ${0##*/} prod|staging|dev" >&2
    exit 2
    ;;
esac

for f in app.env postgres.env; do
  [[ -f "$dir/$f" ]] || { echo "missing $dir/$f" >&2; exit 1; }
done
grep -q '^CYLIST_DATABASE_URL=.' "$dir/app.env" ||
  { echo "no CYLIST_DATABASE_URL in $dir/app.env" >&2; exit 1; }

# Applied rather than created, so a re-run updates. The values only ever pass
# through pipes and process substitution, never a file or an argument list.
apply() { kubectl -n "$ns" apply -f - > /dev/null; echo "   $ns/$1"; }

# Everything after the last `@` up to the next `/` is host[:port]; a password
# may hold `@` but the host never does.
app_env() {
  grep -v '^CYLIST_FCM_SERVICE_ACCOUNT_FILE=' "$dir/app.env" |
    sed -E "s#^(CYLIST_DATABASE_URL=.*@)[^@/]*/#\1$POSTGRES_HOST/#"
}

echo "Secrets for $ns from $dir:"
kubectl -n "$ns" create secret generic cylist-app \
  --from-env-file=<(app_env) --dry-run=client -o yaml | apply cylist-app
kubectl -n "$ns" create secret generic cylist-postgres \
  --from-env-file="$dir/postgres.env" --dry-run=client -o yaml | apply cylist-postgres

if [[ "$ns" == cylist-prod ]]; then
  [[ -f "$dir/fcm-service-account.json" ]] ||
    { echo "missing $dir/fcm-service-account.json" >&2; exit 1; }
  kubectl -n "$ns" create secret generic cylist-fcm \
    --from-file=fcm-service-account.json="$dir/fcm-service-account.json" \
    --dry-run=client -o yaml | apply cylist-fcm
fi
