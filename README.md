# Automated Canary Delivery & Self-Healing Rollback Engine

## Architecture Overview

The following diagram illustrates the complete system architecture:

```mermaid
flowchart TB

%% ==============================
%% TOP INPUT LAYER
%% ==============================

subgraph INPUTS["Software Delivery"]

A["👨‍💻 Developer<br/>Code Changes"]

B["GitHub Repository"]

C["⚙️ GitHub Actions<br/>CI/CD"]

D["🐳 Docker Registry"]

end



%% ==============================
%% CORE PLATFORM
%% ==============================

subgraph CORE["🚀 Progressive Delivery Platform"]


E["Argo Rollouts Controller"]


F["Canary Strategy<br/><br/>
10% → 25% → 50% → 100%"]


G["Traffic Decision Engine<br/><br/>
Continue / Pause / Rollback"]


E ==> F
F ==> G


end



%% ==============================
%% RUNTIME
%% ==============================


subgraph KUBE["☸️ Kubernetes Cluster"]


H["NGINX Ingress<br/><br/>
Traffic Router"]


subgraph SERVICES["Application Versions"]

I["🟢 Stable Release<br/>v1.0<br/><br/>90% Traffic"]

J["🟡 Canary Release<br/>v2.0<br/><br/>10% Traffic"]

end


H ==> I
H ==> J


end



%% ==============================
%% OBSERVABILITY
%% ==============================


subgraph MONITOR["📊 Observability"]

K["Application Metrics<br/>/metrics"]

L["Prometheus"]

M["PromQL Analysis"]

N["Grafana Dashboard"]


K ==> L
L ==> M
L ==> N


end



%% ==============================
%% TESTING
%% ==============================


subgraph TESTING["🔥 Testing"]

O["Load Generator<br/>k6 / Locust"]

P["Failure Injection<br/>Errors / Latency"]

end



%% ==============================
%% MAIN FLOW
%% ==============================


A ==> B

B ==> C

C ==> D

D ==> E


G ==> H


I ==> K
J ==> K


M ==> G


O ==> H

P ==> J



%% ROLLBACK LOOP


G ==>|Failure Detected| E

E ==>|Rollback Canary| J


