# ADR-0006: SeaweedFS for S3-compatible object storage

**Status:** Accepted · **Date:** 2026-11-14 · **Supersedes:** the MinIO choice in the original architecture

## Context
- Fairshare stores datasets, per-model checkpoints and results in object storage.
- The design used MinIO. During Phase 0, no MinIO image could be pulled:
  - `minio/minio` no longer exists on Docker Hub
  - `quay.io/minio/minio` requires authorization
  - Bitnami's MinIO image is gone
- The target stack names "object storage" without naming a product, so any S3-compatible store fits.

## Decision
- Use **SeaweedFS** (`chrislusf/seaweedfs`) in `server -s3` mode, for dev (Compose) and in-cluster (Helm).
- All code talks to it through the **plain S3 API**: `boto3` in Python, `aws-sdk-go-v2` in Go, with a configurable endpoint.
- Pin an exact image version in Phase 1 (the spike used `latest`).

## Consequences
- ✅ Apache-2.0 licensed, ARM64 + x86 images, one container for local use.
- ✅ Proven in Spike B and C: boto3 worked unchanged, including from pods via `host.docker.internal`.
- ✅ Switching to AWS S3 later is a config change (endpoint + credentials).
- ❌ Less widely known than MinIO.
- ❌ Access control is configured through SeaweedFS's own `s3.json` identities file, not an admin API.

## Alternatives considered
| Option | Why not |
|---|---|
| **MinIO** | Images no longer available. Building from source adds a maintenance burden |
| **Garage** | Needs extra cluster layout steps before first use |
| **LocalStack** | An AWS emulator for tests, not a real storage service |
