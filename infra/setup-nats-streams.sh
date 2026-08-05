#!/usr/bin/env bash
# Idempotently creates the JetStream streams the platform needs.
# Run against the kind cluster via the nats-box pod, e.g.:
#   kubectl -n data-platform cp infra/setup-nats-streams.sh <nats-box-pod>:/tmp/setup.sh
#   kubectl -n data-platform exec <nats-box-pod> -- sh /tmp/setup.sh
set -euo pipefail

# All connector "file landed" events and future ingestion/validation events
# live under platform.>. One stream is enough at this scale (BUILD_PLAN.md §3);
# split by subject into dedicated streams only if retention/ack semantics
# actually need to differ per event type.
nats stream info PLATFORM_EVENTS >/dev/null 2>&1 || nats stream add PLATFORM_EVENTS \
  --subjects "platform.>" \
  --storage file \
  --retention limits \
  --max-msgs=-1 \
  --max-bytes=-1 \
  --max-age=168h \
  --max-msg-size=-1 \
  --discard old \
  --dupe-window=2m \
  --replicas=1 \
  --defaults

# Dead-letter handling: consumers reading from PLATFORM_EVENTS should be
# created with a max_deliver limit (e.g. `nats consumer add ... --max-deliver=5`).
# JetStream then emits an advisory on
# $JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.PLATFORM_EVENTS.<consumer> once a
# message exhausts its redeliveries. The Ingestion Service (Phase 2) is the
# first real consumer, so it's the one that subscribes to that advisory subject
# and republishes exhausted messages onto `platform.dlq.<original-subject>` —
# there's nothing to consume events yet in Phase 1, so wiring that here would
# be untested, speculative code.
echo "PLATFORM_EVENTS stream ready"
