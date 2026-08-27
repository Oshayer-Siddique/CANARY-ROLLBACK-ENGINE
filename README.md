#  Automated Canary Delivery & Self-Healing Rollback Engine

See the [phase-by-phase issue history](docs/troubleshooting/README.md) for recorded errors, causes, corrections, verification, and prevention.

> A local Kubernetes canary delivery MVP that observes application health and automatically restores stable traffic when a canary fails.

Current implementation: Phases 1–6 provide the application, containers, Kubernetes routing, monitoring, canary controls, and automated recovery. Phase 7 verifies the integrated workflow; see the [end-to-end verification guide](docs/END_TO_END_VERIFICATION.md) for acceptance results and scope.

The MVP uses an operator-controlled traffic split and a Python recovery controller. A healthy release remains at its current split until the operator increases traffic; an unhealthy canary automatically returns to 100% stable traffic. Argo Rollouts, automatic promotion, CI/CD integration, and Grafana belong to the future roadmap below.

## Run the local MVP

Follow these guides in order:

1. [Application](docs/APPLICATION.md) and [Docker images](docs/DOCKER.md).
2. [Local Kubernetes and Gateway](docs/KUBERNETES.md).
3. [Prometheus monitoring](docs/MONITORING.md).
4. [Canary traffic controls](docs/CANARY_TRAFFIC.md).
5. [Recovery controller](docs/AUTOMATED_RECOVERY.md).
6. [Integrated acceptance](docs/END_TO_END_VERIFICATION.md): run `python3 -u scripts/verify_mvp.py` after the earlier setup is ready.

The acceptance suite injects local faults and restores healthy v2 with 100/0 routing. It saves per-run reports under `reports/`. Read the guide's preconditions before running it. See [architecture](ARCHITECTURE.md) for the implemented components and future design.

---

#  Project Overview

Modern applications are deployed frequently, but every deployment introduces risk.

A single faulty release can cause:

- Application failures
- Increased error rates
- Slow response times
- Production downtime
- Poor user experience


This project solves this problem by creating an automated deployment system that:

1. Releases new versions gradually
2. Sends only a small amount of traffic to new releases
3. Monitors application behavior
4. Automatically decides whether to continue or rollback


---

# 🎯 Project Goal

The main goal is to build an autonomous release management system for Kubernetes.

The system should replace this:

```
Deploy → Monitor Manually → Detect Failure → Rollback
```


with this:

```
Deploy

↓

Canary Release

↓

Automatic Health Analysis

↓

Continue Deployment

OR

Automatic Rollback
```

---

# ❓ What Problem Does This Solve?


## 1. Risky All-at-Once Deployments

Traditional deployments expose all users to the new version immediately.

Example:

```
Before:

100% Users
    |
    |
Application v1


After Deployment:

100% Users
    |
    |
Application v2

```


If v2 has a hidden bug, every user is affected.


### Solution

Use canary deployment:

```
             Users

               |

        Traffic Router

          /          \

       90%            10%

       v1             v2

    Stable         Canary

```


Only a small percentage of users receive the new release.

---

# 2. Slow Human-Based Rollback

Traditional failure handling:


```
Deployment Failure

        ↓

Monitoring Alert

        ↓

Engineer Investigation

        ↓

Manual Rollback

```


This can take several minutes.


### Solution

Automated recovery:

```
Application Metrics

        ↓

Prometheus

        ↓

Health Analysis

        ↓

Automatic Rollback

```

---

# 3. Deployment Anxiety

Many teams avoid frequent releases because production failures are risky.

This project enables:

✅ Safer deployments  
✅ Faster releases  
✅ Lower downtime risk  
✅ Automated recovery  

---

# 👥 Who Will Use This System?


## DevOps Engineers

### Why?

Manage:

- Deployment strategies
- Kubernetes releases
- Infrastructure automation


### How?

They configure:

- Canary rules
- Rollback policies
- Monitoring thresholds


---

## Site Reliability Engineers (SRE)

### Why?

Improve:

- Reliability
- Availability
- Incident response


### How?

They define:

- Error thresholds
- Latency limits
- Service health rules


---

## Software Engineers

### Why?

Developers need confidence when releasing new features.


### Workflow:

```
Write Code

↓

Push Code

↓

CI/CD Builds Image

↓

Canary Deployment

↓

Automatic Validation

```

---

## Engineering Managers

### Why?

They need:

- Faster delivery
- Fewer outages
- Better engineering efficiency


---

#  How the System Works


Target architecture for the broader roadmap (CI/CD is future work):


```
Developer

    |

GitHub Repository

    |

CI/CD Pipeline

    |

Docker Image

    |

Kubernetes

    |

Canary Deployment

    |

Prometheus Monitoring

    |

Decision Engine

    |

 -----------------

|                 |

Continue       Rollback

```

---

# 🛠️ Technology Stack


| Component | Technology |
|---|---|
| Application | FastAPI (Python) |
| Containerization | Docker |
| Orchestration | Kubernetes |
| Local Cluster | k3d / k3s |
| Recovery Engine | Python controller with Kubernetes API access |
| Traffic Routing | NGINX Gateway Fabric / Gateway API HTTPRoute |
| Monitoring | Prometheus recording and alerting rules |
| Traffic Generation | Repository Python scripts |
| Acceptance | pytest, Ruff, and live integrated verifier |

---

# 🗺️ Development Roadmap


Version 1 is the local MVP. Versions 2 and 3 below are planned work; their feature lists do not indicate implemented capabilities.


# 🟢 Version 1 — MVP

## Goal

Build a working canary deployment prototype.

Focus:

> "Make it work"


---

## Features


### ✅ FastAPI Application

Create a sample production-like API.

Includes:

- REST endpoints
- Health checks
- Prometheus metrics
- Version information


---

### ✅ Docker Support

Create:

```
application:v1

application:v2
```

Features:

- Dockerfile
- Image building
- Container execution


---

### ✅ Kubernetes Deployment


Implement:

- Pods
- Deployments
- Services
- Gateway API routing


---

### ✅ Canary Traffic Routing


Example:

```
Stable Version

90% Traffic


Canary Version

10% Traffic

```


---

### ✅ Basic Monitoring


Collect:

- Request count
- Error count
- Application version


---

### MVP Success Criteria


The system can:


```
Deploy v2

↓

Send traffic

↓

Create failure

↓

Detect failure

↓

Rollback automatically

```

---

# 🟡 Version 2 — Production Ready


## Goal

Transform the prototype into a realistic DevOps platform.

Focus:

> "Make it reliable"


---

## Features


### ✅ Argo Rollouts Integration


Replace manual scripts with:

- Rollout CRD
- AnalysisTemplate
- Automated rollback


---

### ✅ Progressive Delivery


Traffic progression:


```
10%

↓

25%

↓

50%

↓

100%

```


---

### ✅ Prometheus Quality Gates


Example:


```
IF

HTTP 5xx rate > 2%

FOR 60 seconds


THEN

Rollback

```


---

### ✅ Automated CI/CD


Pipeline:


```
Git Push

↓

Build Image

↓

Run Tests

↓

Deploy Canary

```


---

### ✅ Load Testing


Tools:

- k6
- Locust


Simulate real user traffic.

---

### ✅ Grafana Dashboard


Display:

- Request rate
- Error rate
- Latency
- Deployment status


---

# 🔴 Version 3 — Advanced SRE Platform


## Goal

Create an intelligent autonomous deployment platform.

Focus:

> "Make it intelligent"


---

## Features


### 🧠 Intelligent Health Analysis

Move beyond fixed thresholds.

Example:


Current:


```
Error rate > 2%

Rollback

```


Future:


```
Compare current release

against historical behavior


Detect anomaly

```


---

### 💥 Chaos Engineering


Introduce failures:


- Pod crashes
- Network delay
- Resource exhaustion


Tools:

- Chaos Mesh
- Litmus


---

### 🌐 Multi-Service Support


Support:


```
Frontend

Backend

Payment Service

Authentication Service

```


---

### 🔔 Notifications


Integrate:

- Slack
- Email


Events:


```
Deployment Started

Canary Failed

Rollback Completed

```


---

### 🖥️ Deployment Dashboard


Display:

- Current version
- Traffic split
- Health status
- Deployment history
- Rollback events


---

# 🎯 Final Project Outcome


The final system will provide:


```
Safe Deployment

        +

Real-Time Monitoring

        +

Automatic Decision Making

        +

Self-Healing Rollback

```


---

# Skills Demonstrated


This project demonstrates:


✅ Kubernetes  
✅ Docker  
✅ CI/CD Automation  
✅ Progressive Delivery  
✅ Observability  
✅ Prometheus  
✅ Infrastructure Automation  
✅ Reliability Engineering  
✅ Production Deployment Practices  


---

# Project Evolution


```
Version 1

Make It Work


        ↓


Version 2

Make It Reliable


        ↓


Version 3

Make It Intelligent

```
