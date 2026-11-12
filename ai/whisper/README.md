# whisper

Self-hosted speech-to-text for Russian call recordings: the
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) HTTP server running
`large-v3-turbo` (q5_0) on `kube-worker-3`, the cluster's Raspberry Pi 5.
It speaks the OpenAI transcription API (`POST /v1/audio/transcriptions`), and
LiteLLM fronts it as `local/whisper-large-v3-turbo` (see
[`../litellm/README.md`](../litellm/README.md#self-hosted-speech-to-text-localwhisper-large-v3-turbo)).

No out-of-band Secrets. `application.yaml` points Argo CD at
[`manifests/`](manifests/): a `local-path` PVC, the Deployment and a ClusterIP
Service. There is **no Ingress and no auth** — the server is reachable only
inside the cluster, and LiteLLM is the authenticated front door.

## Usage

Through LiteLLM (in-cluster):

```sh
curl -s http://litellm.litellm.svc.cluster.local:4000/v1/audio/transcriptions \
  -H "Authorization: Bearer <litellm key>" \
  -F model=local/whisper-large-v3-turbo \
  -F file=@call.mp3
# {"text":" ..."}
```

Directly, for debugging: the same form to
`http://whisper.whisper.svc.cluster.local:8080/v1/audio/transcriptions`
(the `model` field is ignored there). Other form fields the server reads include
`language` (overrides the `ru` default), `response_format`
(`json` | `text` | `srt` | `vtt` | `verbose_json`) and `prompt`.

Send compressed audio (mp3 / ogg / m4a / opus). The server converts any input
with ffmpeg (`--convert`), and an hour of 16 kHz mono WAV is ~115 MB.

## Long calls: in-cluster clients only

Expect roughly **2.6× slower than real time** (measurements below): a 30-minute
call takes about 1.3 h, an hour-long one about 2.6 h. The HTTP request stays open
the whole time, which decides who can call it:

- **In-cluster clients** (n8n, jobs) calling `litellm.litellm.svc.cluster.local:4000`
  or the whisper Service directly are fine. The LiteLLM model carries its own
  `timeout: 18000` (5 h).
- **Not through the public LiteLLM ingress.** It has `proxy-read-timeout: 600`,
  so anything over 10 minutes of processing (≈ 4 minutes of audio) gets a 504.
  The Keenetic router in front may have its own limit as well (not measured).
- The server runs **one transcription at a time** (a mutex in `server.cpp`).
  Further requests wait in line, each holding its own connection.

## Why the binary is built in an init container

The upstream image cannot run on this node as shipped:

- `ghcr.io/ggml-org/whisper.cpp:main` is **amd64 only**. The arm64 build is a
  separate tag, `main-arm64[-<commit>]`.
- That arm64 build **dies with `Illegal instruction` (exit 132) on the Pi 5**.
  CI builds it on GitHub's `ubuntu-24.04-arm` runners with ggml's default
  `GGML_NATIVE=ON`, i.e. `-mcpu=native` for that runner's Neoverse CPU. The Pi 5's
  Cortex-A76 has only `asimddp` / `fphp` (no i8mm, no SVE).
- The image ships the full source in `/app`, plus gcc, cmake, ffmpeg and curl,
  but **no g++**.

So the `build` init container (same pinned image) installs `g++` with apt and
compiles `whisper-server` for an explicit target instead of `native`:

```sh
-DGGML_NATIVE=OFF -DGGML_CPU_ARM_ARCH=armv8.2-a+dotprod+fp16 -DBUILD_SHARED_LIBS=OFF
```

The result is a static binary on the PVC under `/data/bin/<build id>/`, with
`/data/current` pointing at it. The build id is the sha256 of the image's own
`whisper-server` plus the arch flags. That makes it change automatically when the
image is bumped, so a new image triggers exactly one rebuild, and older builds
are pruned. Measured on the node:

| start | time to Ready |
| --- | --- |
| cold (apt + build ~2 min, model download ~3 min) | ~8 min |
| warm (build cached, models only re-hashed) | ~11 s |

The alternative — our own image built in CI for `armv8.2-a` — was rejected to
keep everything in this repo with no extra registry or pipeline. The cost is
that a rebuild needs the Ubuntu archive and Hugging Face to be reachable, and it
happens only when the image or the flags change.

**Bumping whisper.cpp:** change the image (tag **and** digest) in all three
containers of `deployment.yaml`. Pick a `main-arm64-<commit>` tag of a release
commit. `main-arm64` alone is rebuilt daily and is not reproducible.

## Models

The `models` init container downloads into `/data/models/` and checks each file
against a pinned sha256 on every start. A mismatch re-downloads; a failed check
fails the pod. The files have fixed local names (`model.bin`, `vad.bin`), so
switching models means editing only the `MODEL_*` / `VAD_*` env values. URLs are
pinned to a Hugging Face **revision**, not `main`, so a restart can never pick
up different weights.

| file | source | size |
| --- | --- | --- |
| `model.bin` | `ggerganov/whisper.cpp@5359861` `ggml-large-v3-turbo-q5_0.bin` | 547 MiB |
| `vad.bin` | `ggml-org/whisper-vad@9ffd54a` `ggml-silero-v6.2.0.bin` | 0.9 MiB |

## Server flags

| flag | why |
| --- | --- |
| `--language ru` | the default for calls. Without it whisper.cpp defaults to `en`. A request's `language` field overrides it |
| `--threads 3` + CPU limit `3` | leaves one core to the CNPG primaries, argocd and basic-memory that share the node. 4 threads were only ~11% faster (190 s vs 213 s on the test clip) |
| `--vad --vad-model` | Silero VAD skips non-speech. It sped the test clip up from 280 s to 213 s, and real calls have more silence. It also prevents Whisper's hallucinated text on long pauses |
| `--convert` | accept any container/codec. ffmpeg 4.4 is in the image |
| `--inference-path /v1/audio/transcriptions` | the OpenAI route, so LiteLLM's `openai/` provider works unchanged (the default is `/inference`) |
| `--host 0.0.0.0` | the default is `127.0.0.1` |

## Resources and placement

- Pinned to `kube-worker-3` (`nodeSelector`). It is the only node with the RAM,
  and the PVC is node-local (`local-path` → `/mnt/ssd/local-path`, reclaim
  `Delete` — everything on it is rebuildable).
- Requests `500m` / `1Gi`: on 2026-11-13 worker-3 had only ~1 CPU and ~1.7 GiB
  of **requests** still free (74% / 77% allocated), even though actual use was
  low.
- Memory limit `2Gi`: peak RSS was 885 MiB on a one-minute clip. Long audio
  adds the decoded PCM (~230 MB per hour) and the multipart upload, which stays
  in memory.
- `Recreate` strategy: one RWO volume, one node.
- Probes hit `/health` (503 while the model loads, 200 after). During a running
  transcription it kept answering in 10–40 ms (checked every 8 s through a full
  run), so the lenient liveness probe will not kill a long job.

The kernel on this node uses 16K pages (`6.18.x-rpt-rpi-2712`). That is what
keeps LiteLLM off it. ggml and whisper.cpp run fine on it.

## Measurements (2026-11-12, Pi 5, large-v3-turbo q5_0)

65 s of synthetic Russian speech (Windows TTS, almost no pauses):

| setup | wall time | × real time |
| --- | --- | --- |
| `whisper-cli`, 3 threads, no VAD | 280 s | 4.3 |
| `whisper-cli`, 3 threads, VAD | 213 s | 3.3 |
| `whisper-cli`, 4 threads, VAD | 190 s | 2.9 |
| **`whisper-server` as deployed**, mp3 upload | **166 s** | **2.6** |

The encoder is the bottleneck: ~81–92 s per 30 s window. `turbo` cuts only the
decoder, and its encoder is the full large-v3 one. Accuracy on the test clip was
near perfect: numbers, «quotes» and punctuation were right, with one misheard word.
