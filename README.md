# Stratos

Stratos is a compact distributed task system and learning environment for exploring how reliable background work is coordinated across multiple workers. It provides the foundation of an agent runtime—task submission, durable delivery, worker competition, progress reporting, and recovery experiments—without hiding the distributed-systems behavior behind a large framework.

## What the system does

A FastAPI controller accepts tasks and exposes their state. Redis Streams acts as the durable broker, and multiple workers compete for work so each task is claimed by one consumer. A browser dashboard receives live updates through server-sent events, making the lifecycle from queued to running to completed or failed visible as it happens. Redis persistence keeps task and broker state across ordinary service restarts.

The work handler is deliberately isolated from queue coordination. Today it can process demonstration jobs; the same boundary can be replaced with an AI agent, document processor, automation step, or other long-running operation while preserving the surrounding task lifecycle and observability.

## Distributed-systems lab

Stratos also includes an isolated learning lab for testing failure modes without interfering with normal tasks. It focuses on two problems that simple queue demos often omit:

- duplicate effects when work succeeds but its acknowledgement is lost; and
- recovery when a worker dies or becomes unreachable while holding a task.

Each study can replay a preserved broken baseline and evaluate an evolving candidate strategy against deterministic invariants. Timelines and measurements show what occurred, while the lab leaves the repair itself to the developer. Dedicated workers and Redis keys keep these experiments separate from the main queue.

## Project goal and boundaries

The goal is to make the mechanics of at-least-once delivery, idempotency, acknowledgements, heartbeats, ownership, and stale-work recovery concrete enough to observe and modify. Stratos is both a usable starter and a teaching tool: small enough to read end to end, but complete enough to expose failures that matter in production systems.

It is not presented as a production-ready orchestration platform. A real deployment would still need authentication, authorization, task timeouts, retry and dead-letter policies, stale-message claiming, retention controls, metrics, and capacity planning. The repository establishes the execution model on which those guarantees—or an agent platform with those guarantees—can be built.
