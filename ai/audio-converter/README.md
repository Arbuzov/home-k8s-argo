# Audio converter

Private REST adapter for converting M4A to MP3 for the n8n SpeechKit workflow.
It reuses the pinned ARM64 whisper.cpp image already present on the Pi 5 for its
Python and FFmpeg binaries; it does not start the transcription server or load
models. The cluster-internal endpoint is `POST /convert` on
`audio-converter.n8n.svc.cluster.local:8080`, with a raw audio body and bearer
token from the out-of-band `audio-converter-auth` secret, key `token`.

Do not expose this service through an ingress: Flannel does not enforce
NetworkPolicy, and ClusterIP alone is not access control. Supply the same token
to n8n as an HTTP Header Auth credential. Keep the secret out of Git and back it
up with the other n8n credentials.

Input is capped at 20,000,000 bytes, one conversion runs at a time, and excess
concurrent requests receive 503. FFmpeg gets one CPU thread and a 300-second
deadline. It outputs MP3 at 96 kbit/s while retaining the input's channel
count; temporary files are removed after the response. The 256 MiB scratch
volume is bounded and empty after normal completion, but disappears on pod
recreation. The workload is pinned to the Pi 5 because its pinned image and
audio conversion were tested there; no Whisper model or PVC is mounted.

The ConfigMap name is content-hashed by Kustomize, so a code change rolls out a
new pod. Test with synthetic stereo M4A, malformed input, missing/wrong token,
concurrent uploads, and the real n8n binary flow before enabling the workflow.
