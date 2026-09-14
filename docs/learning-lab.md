# Learning lab notes

The lab is evidence-first: it names the contract before a run, then reports only
the observable timeline, measurements, and invariant outcomes. A failed attempt
is not diagnosed. When more than one invariant remains false, the UI explicitly
marks the solution incomplete.

## Isolation and data flow

Normal work uses the `tasks` stream and `workers` consumer group. Lab workers each
consume a dedicated `lab:worker:<id>:tasks` stream under the `lab-workers` group.
Experiment state uses a random run ID under `lab:run:<id>:*`. The controller allows
one experiment at a time so the animated topology corresponds to one causal trace.

The baseline mappings in `app/challenges/` preserve the originally published bug.
Candidate mappings are the supported place for future learner designs. They begin
identical so both modes fail in the same reproducible way on the first release.

## Research basis

- [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) covers consumer groups, pending entries, acknowledgements, claiming, and retention.
- [Apache Kafka delivery semantics](https://kafka.apache.org/35/design/design/) explains at-most-once, at-least-once, and the boundaries of exactly-once guarantees.
- [Unreliable Failure Detectors for Reliable Distributed Systems](https://www.cs.princeton.edu/courses/archive/fall08/cos597B/papers/unreliable.pdf) formalizes the tension between detecting crashes and falsely suspecting live processes.
- [FLP](https://groups.csail.mit.edu/tds/papers/Lynch/jacm85.pdf) establishes the limit of deterministic consensus in a fully asynchronous system with one possible crash.
- [Google SRE: Addressing Cascading Failures](https://sre.google/sre-book/addressing-cascading-failures/) motivates bounded queues, load shedding, and overload experiments.
- [AWS retry guidance](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_mitigate_interaction_failure_limit_retries.html) motivates retry budgets, exponential backoff, and jitter.
- [Transactional outbox](https://microservices.io/patterns/data/transactional-outbox) describes the database/broker dual-write failure that a future case will model.
