Automated Canary Delivery & Self-Healing Rollback Engine
MVP Technical Design Plan
Version: 1.0
1. MVP Objective
Primary Goal

Build a working proof-of-concept autonomous deployment system that can:

Deploy a new application version alongside an existing version.
Route limited traffic to the new version.
Monitor application health metrics.
Detect unhealthy releases automatically.
Remove failed releases from traffic.
Restore traffic to the stable version.

The MVP demonstrates the fundamental idea:

Release → Observe → Decide → Recover
2. MVP Scope

The MVP is NOT a complete enterprise deployment platform.

The MVP focuses on proving the core mechanism:

Canary Deployment + Monitoring + Automated Rollback
3. MVP Architecture Overview

The MVP consists of these major components:

                         Developer

                            |
                            |

                     Git Repository

                            |

                     Docker Build

                            |

                    Kubernetes Cluster

                            |

                 ----------------------

                 |                    |

           Stable Version        Canary Version

              v1.0                  v2.0


                 \                  /

                  \                /

                   Traffic Router


                         |

                    User Traffic


                         |

                 Application Metrics


                         |

                    Prometheus


                         |

                 Health Evaluation


                         |

              Continue OR Rollback


4. MVP Components

The MVP will contain six major systems:

Component 1: Sample Application
Purpose

Create a realistic application that can demonstrate:

successful deployments
failed deployments
metric collection

Technology:

Python FastAPI
Application Requirements

The application must provide:

API Endpoint

Example:

GET /


Purpose:

Returns application version.

Example behavior:

Version 1:

{
 "version":"v1"
}


Version 2:

{
 "version":"v2"
}

Health Endpoint
GET /health


Purpose:

Kubernetes health checking.

Response:

Healthy:

200 OK


Unhealthy:

500 ERROR

Metrics Endpoint
GET /metrics


Purpose:

Expose Prometheus metrics.

Metrics required:

Request Counter

Tracks:

Total requests
HTTP status codes

Example:

requests_total{
version="v1",
status="200"
}

Error Counter

Tracks:

HTTP 500 responses

Failure Simulation

The application must support artificial failures.

Example:

Environment variable:

FAILURE_RATE=30


Meaning:

30% of requests return errors.

Purpose:

Allow testing automatic rollback.

Component 2: Docker Containerization
Purpose

Package application into portable containers.

The MVP creates two images:

application:v1

application:v2

Requirements

Each image must:

Start independently
Expose application port
Include health endpoint
Include metrics endpoint
Component 3: Local Kubernetes Environment
Purpose

Create a realistic Kubernetes environment locally.

Technology:

k3d


Reason:

Lightweight
Fast
Uses real Kubernetes
Cluster Requirements

The cluster must contain:

Kubernetes API Server

Worker Nodes

NGINX Ingress Controller

Prometheus

Application Pods

Component 4: Application Deployment
Purpose

Deploy two application versions.

The cluster should contain:

Stable Deployment

Example:

application-v1


replicas: 3


Purpose:

Represents production.

Canary Deployment

Example:

application-v2


replicas: 1


Purpose:

Represents new release.

Traffic Model

Initial state:


                 Users


                   |

             NGINX Ingress


              /          \


          100%             0%

           v1              v2



        After canary starts:


                 Users


                   |

             NGINX Ingress


              /          \


          90%             10%

           v1              v2


Component 5: Prometheus Monitoring
Purpose

Collect application health information.

Prometheus must scrape:

/metrics


from application pods.

Required Metrics
Request Rate

Question:

"How many requests are happening?"

Metric:

requests_total

Error Rate

Question:

"How many requests are failing?"

Metric:

http_errors_total

Release Health

The system must know:

Is v2 behaving worse than v1?

Component 6: Decision Engine
Purpose

Make automatic deployment decisions.

MVP decision logic:

IF

Canary error rate > threshold


THEN

Rollback


ELSE

Continue

MVP Health Rule

The first version uses:

Error Threshold:

5%


Evaluation Window:

60 seconds


Example:

Traffic:

1000 requests


Errors:

100


Error Rate:

10%


Threshold:

5%


Decision:

Rollback

5. MVP Deployment Flow

Complete lifecycle:

Step 1

Developer creates new version.

v2 application created

Step 2

Docker image built.

application:v2

Step 3

Deploy v2 into Kubernetes.

Both versions exist:

v1 running

v2 running

Step 4

Send small traffic percentage.

Example:

90% → v1

10% → v2

Step 5

Generate traffic.

Tools:

k6

or

Locust

Step 6

Prometheus collects metrics.

Example:

v2:

Requests:

10000


Errors:

800


Error Rate:

8%

Step 7

Decision happens.

Rule:

8% > 5%

FAIL

Step 8

Rollback happens.

Traffic:

Before:

90% v1
10% v2


After:

100% v1
0% v2

6. MVP Implementation Order

The coding agent should implement in this order:

Phase 1: Application Layer

Build:

FastAPI application
Metrics
Health endpoint
Failure simulation

Success:

Application runs locally.

Phase 2: Container Layer

Build:

Dockerfile
Images
Version tagging

Success:

Both versions run.

Phase 3: Kubernetes Layer

Build:

Cluster
Deployments
Services
Ingress

Success:

Application accessible through Kubernetes.

Phase 4: Monitoring Layer

Install:

Prometheus

Configure:

Metrics scraping

Success:

Prometheus sees application metrics.

Phase 5: Canary Traffic Layer

Implement:

v1

+

v2

+

Traffic splitting


Success:

Both versions receive traffic.

Phase 6: Automated Recovery

Implement:

Health evaluation:

Prometheus metrics

        |

Decision

        |

Rollback


Success:

Failure automatically removes canary traffic.

7. MVP Testing Scenarios

The MVP must demonstrate these cases:

Scenario 1: Successful Release

Expected:

Deploy v2

↓

10% traffic

↓

Metrics healthy

↓

Increase traffic

Scenario 2: Failed Release

Expected:

Deploy v2

↓

10% traffic

↓

Inject failures

↓

Error threshold exceeded

↓

Rollback

Scenario 3: Stable Recovery

Expected:

After rollback:


Users receive only v1


Application remains available

8. MVP Success Criteria

The MVP is complete when:

✅ Two application versions run simultaneously

✅ Traffic can be split between versions

✅ Metrics are collected

✅ Failure can be simulated

✅ System detects unhealthy canary

✅ Automatic rollback happens

✅ Users are redirected to stable version

9. Explicitly Out of Scope For MVP

Do NOT implement:

❌ AI anomaly detection

❌ Multi-region deployment

❌ Service mesh

❌ Complex frontend dashboard

❌ Database rollback

❌ Multiple microservices

❌ Cloud deployment

❌ Advanced security

These belong to Version 2 and Version 3.

Final MVP Definition

The MVP is a small but complete autonomous delivery system:

A developer releases a new version.

The system exposes it to limited traffic.

The system observes real behavior.

The system decides whether the release is safe.

The system automatically recovers from failures.
