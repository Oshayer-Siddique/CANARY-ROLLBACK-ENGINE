# CANARY-ROLLBACK-ENGINE
# Automated Canary Delivery & Self-Healing Rollback Engine

## Architecture Overview

The following diagram illustrates the complete system architecture:

```mermaid
flowchart LR

subgraph DEV["👨‍💻 Development Workflow"]

A["Developer"]
B["GitHub Repository"]
C["Pull Request"]

end

subgraph CICD["⚙️ CI/CD Pipeline"]

D["GitHub Actions"]
E["Build & Test"]
F["Docker Image Build"]
G["Container Registry"]

end

subgraph DELIVERY["🚀 Progressive Delivery"]

H["Argo Rollouts Controller"]

I["Canary Strategy"]

J["Traffic Progression<br/>10% → 25% → 50% → 100%"]

K["Automated Quality Gate"]

end


subgraph KUBE["☸️ Kubernetes Cluster"]

L["NGINX Ingress Controller"]

M["Stable Version v1"]

N["Canary Version v2"]

end


subgraph OBS["📊 Observability"]

O["Application Metrics"]

P["Prometheus"]

Q["PromQL Analysis"]

R["Grafana"]

end


A --> B
B --> C
C --> D

D --> E
E --> F
F --> G

G --> H

H --> I
I --> J
J --> L

L -->|90% Traffic| M
L -->|10% Traffic| N


M --> O
N --> O

O --> P
P --> Q

Q --> K

K -->|Healthy| H
K -->|Failed| H

P --> R
