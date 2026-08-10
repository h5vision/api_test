# Vision Kubernetes-ready deployment

## Responsibility boundary

Vision owns queue semantics, job state, deduplication, lease/reclaim, worker heartbeat,
runtime provider configuration, and shared-workspace coordination. Kubernetes owns Pod
lifecycle, placement, restart, rolling rollout and replica count. KEDA may own worker
autoscaling from Redis Stream consumer-group lag.

## Required platform capabilities

1. Kubernetes cluster with an Ingress controller (manifests assume `traefik`).
2. An RWX-capable StorageClass for `vision-shared-workspace`, or replace the shared
   filesystem adapter with object storage before multi-node rollout.
3. Reachable PostgreSQL and Redis endpoints. Qdrant/embedding/LLM endpoints are not
   bootstrap configuration. Administrators register/select them through PostgreSQL-backed
   runtime registries; P2-C uses `vector_targets` as the Qdrant target authority.
4. KEDA v2.x only if event-driven worker autoscaling is enabled.

## Images

Build immutable images from the repository root:

```bash
docker build -f deploy/docker/backend.Dockerfile -t <registry>/vision-backend:<tag> .
docker build -f deploy/docker/admin.Dockerfile -t <registry>/vision-admin:<tag> .
```

Patch `api.yaml`, `worker.yaml`, `migration-job.yaml`, and `admin.yaml` with those image
references (prefer digest-pinned images in production).

## Secrets

Copy `secret.example.yaml` outside source control, replace the placeholder values, and
apply it as `vision-secrets`. Production should use External Secrets/Vault/cloud secret
management rather than committing the Secret manifest.

## Deployment order

```bash
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml
kubectl apply -f <your-secret.yaml>
kubectl apply -f shared-workspace-pvc.yaml

# Alembic is a deployment gate, not an initContainer on every API replica.
kubectl delete job -n vision vision-db-migrate --ignore-not-found
kubectl apply -f migration-job.yaml
kubectl wait -n vision --for=condition=complete job/vision-db-migrate --timeout=10m

kubectl apply -k .
# Optional after KEDA is installed:
kubectl apply -f keda-worker.yaml
```

## Shared workspace transition

The current upload/import flow writes bulk bytes under `/shared`. API and worker replicas
coordinate state transitions with Redis distributed locks, so a process-local lock is no
longer the correctness boundary. The PVC **must support ReadWriteMany** when replicas may
land on different nodes.

The next storage evolution can replace the filesystem implementation with S3/MinIO or
another object store without changing Redis queue/job semantics.

## Probes and shutdown

- API `/v1/live`: process-only liveness; downstream provider failure does not restart Pods.
- API `/v1/ready`: Redis coordination + Alembic persistence + shared workspace readiness.
- Worker `backend.worker_probe`: verifies Redis and its heartbeat record.
- Worker SIGTERM stops new dequeues, keeps the current task lease renewed, finishes the
  active job when possible, then exits. `terminationGracePeriodSeconds` is deliberately
  longer than the API grace period.

## KEDA

`keda-worker.yaml` scales from Redis Stream consumer-group lag rather than CPU. Keep
`TASK_QUEUE_NAME`, `TASK_CONSUMER_GROUP`, and the KEDA `stream`/`consumerGroup` values in
sync. The provided policy is conservative (`min=1`, `max=8`) and must be load-tested
against embedding/vector downstream capacity before increasing the maximum.
