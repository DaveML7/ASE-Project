# Bruno collections (component + integration tests)

One JSON collection per internal microservice. Base URLs point at the service
containers exposed locally (`docker compose exec` / port mapping) or, for
integration runs, at `https://localhost:8443/api/v1`.

Component tests use the service directly on its own port; for integration runs
change the base URL to the gateway (`https://localhost:8443/api/v1`), as
required by the project specification. Nothing else in the requests has to change.

Import each file with *Bruno → Import collection → JSON* and run with the Bruno
CLI: `bru run docs/bruno/auth.json --env Local`.
