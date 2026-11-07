# Hosted Agent azd service directory

This directory is the `project` path of the `process-optimizer-agent` service in `azure.yaml`.
It intentionally contains **no Dockerfile**.

The service sets `docker.imagePassthrough: true`. azd then uses the digest-pinned image in
`HOSTED_AGENT_IMAGE` unchanged as the final remote artifact, without building, pulling, tagging or
pushing it.

Passthrough is required. Without it, azd's Docker framework runs a build step even when `image`
is configured. Because this directory has no Dockerfile, that step falls back to a Cloud Native
Buildpacks (`pack` + Oryx builder) build. That build fails on Docker Engine 29+, because azd
1.36.0 uses `pack` v0.30.0, which pins Docker API 1.38. `AZD_AGENT_SKIP_ACR=true` only affects
the `azure.ai.agents` extension stage, which runs after this build step.

Keeping this directory free of Dockerfiles is a second safeguard: azd can never build the root
workflow `Dockerfile` or any other image from here.

The agent image is built separately from `deploy/agent/Dockerfile` for `linux/amd64` and pushed
by digest. See `docs/azure_deployment_architecture.md`. This Azure path is optional and
experimental; the hosted agent never became operational.

Do not add a Dockerfile, source code, or configuration with secrets here.
