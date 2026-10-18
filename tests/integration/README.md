# Integration tests

T02 adds `test_compose.py`: run `make deployment-check` with a Docker Engine.
It uses unique labelled ephemeral volumes and a test-only backend port probe;
application, Flyway, contract and native CLI acceptance remain future work.

Reserved for the assigned component and integration tasks in the
[task graph](../../docs/governance/TASK_GRAPH.json). No application, database,
contract or native CLI test is implemented or claimed passing by S00.
