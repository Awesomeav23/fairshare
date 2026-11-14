#!/usr/bin/env bash
# Usage: h2o-cluster.sh <name> <nodes> [memory]   -> prints manifest for one H2O cluster
# Mirrors ARCHITECTURE §4: StatefulSet + headless discovery Service, plus a client Service.
NAME=$1; NODES=$2; MEM=${3:-1Gi}
cat <<YAML
apiVersion: v1
kind: Service
metadata:
  name: ${NAME}-h2o-discovery
  labels: {fairshare.io/cluster: ${NAME}}
spec:
  clusterIP: None
  publishNotReadyAddresses: true   # non-leader pods never become Ready; discovery must still see them
  selector: {fairshare.io/cluster: ${NAME}}
  ports: [{name: h2o, port: 54321}]
---
apiVersion: v1
kind: Service
metadata:
  name: ${NAME}-h2o
  labels: {fairshare.io/cluster: ${NAME}}
spec:
  selector: {fairshare.io/cluster: ${NAME}}   # only the Ready pod (the leader) gets traffic
  ports: [{name: h2o, port: 54321}]
---
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: ${NAME}-h2o
  labels: {fairshare.io/cluster: ${NAME}}
spec:
  serviceName: ${NAME}-h2o-discovery
  replicas: ${NODES}
  podManagementPolicy: Parallel
  selector: {matchLabels: {fairshare.io/cluster: ${NAME}}}
  template:
    metadata:
      labels: {fairshare.io/cluster: ${NAME}}
    spec:
      terminationGracePeriodSeconds: 10
      containers:
        - name: h2o
          image: fairshare/h2o:3.46.0.12
          imagePullPolicy: Never
          ports: [{containerPort: 54321}, {containerPort: 8080}]
          env:
            - {name: H2O_KUBERNETES_SERVICE_DNS, value: ${NAME}-h2o-discovery.fairshare-jobs.svc.cluster.local}
            - {name: H2O_NODE_EXPECTED_COUNT, value: "${NODES}"}
            - {name: H2O_NODE_LOOKUP_TIMEOUT, value: "180"}
          resources:
            requests: {cpu: 250m, memory: ${MEM}}
            limits: {memory: ${MEM}}
          readinessProbe:
            httpGet: {path: /kubernetes/isLeaderNode, port: 8080}
            periodSeconds: 3
YAML
