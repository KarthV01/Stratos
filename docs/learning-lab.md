# Distributed-systems field guide

The guide at `/lab` is a read-only companion to the live task monitor. It uses the
same restrained visual language as the main application and organizes distributed
work around four ambiguous boundaries:

1. **Delivery** — whether a task arrived zero, one, or several times.
2. **Ownership** — which process is currently authorized to act.
3. **Time** — how long to wait, remember, retry, or suspect.
4. **Truth** — which result, version, or leader is authoritative.

Every study contains a problem statement, an automatically revealed message-flow
trace, concrete safeguards to implement, and the guarantee those safeguards create.
There are no candidate strategies, scores, repair controls, or dedicated lab workers.
The demonstrations are explanatory and cannot affect the normal task queue.

## Research basis

- [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) covers consumer groups, pending entries, acknowledgements, claiming, and retention.
- [Apache Kafka delivery semantics](https://kafka.apache.org/35/design/design/) explains at-most-once, at-least-once, and the boundaries of exactly-once guarantees.
- [Unreliable Failure Detectors for Reliable Distributed Systems](https://www.cs.princeton.edu/courses/archive/fall08/cos597B/papers/unreliable.pdf) formalizes the tension between detecting crashes and falsely suspecting live processes.
- [FLP](https://groups.csail.mit.edu/tds/papers/Lynch/jacm85.pdf) establishes the limit of deterministic consensus in a fully asynchronous system with one possible crash.
- [Google SRE: Addressing Cascading Failures](https://sre.google/sre-book/addressing-cascading-failures/) motivates bounded queues, load shedding, and overload controls.
- [AWS retry guidance](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html) motivates retry budgets, exponential backoff, and jitter.
- [Transactional outbox](https://microservices.io/patterns/data/transactional-outbox) describes the database/broker dual-write failure and durable relay pattern.
