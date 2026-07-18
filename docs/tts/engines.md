# TTS Engines Overview

Engine-specific configuration:

- [eSpeak](espeak.md)
- [SVOX Pico](pico.md)
- [Festival](festival.md)
- [Amazon Polly](polly.md)
- [Google Cloud TTS](google-cloud.md)

For the current engine matrix and shared settings, see [TTS profiles](index.md).

Cloud engines require `cloud_data_processing_accepted: true`, a protected credential file outside
service-user home directories and deployment-specific region, retention and processor review.
