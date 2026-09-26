"""Alexa's voice and ears: Amazon Polly out, Amazon Transcribe in.

The first demo spoke with the browser's own speech synthesiser and listened
with the browser's own recogniser. Both are free and both are uneven: the
voice is whatever the operating system ships (on most machines, plainly a
machine), and recognition exists only where Google or Apple provide a service
behind the API — Chrome, Edge and Safari, but not Firefox, Brave or an
editor's built-in browser.

So both now run here, on AWS, with the credentials Bedrock already uses:

* **Speaking** (``app.voice.speech``). Each sentence of an answer is sent to
  Polly as it completes, and the MP3 goes back to the browser over
  ``POST /api/speech`` (``app.voice.routes``). Polly's generative engine is the
  most natural-sounding voice AWS has; the neural engine is the fallback.
* **Listening** (``app.voice.listen``). The browser records the microphone with
  ``getUserMedia`` and an AudioWorklet — which every current browser has —
  and streams 16 kHz PCM over the visitor's own ``/ws``. This server relays it
  to Transcribe's streaming API and sends the words back as they are heard.

Nothing is installed in the browser, nothing new is installed here (boto3 and
``websockets`` were already dependencies), and nothing third-party is
redistributed: the audio is produced and transcribed by AWS services called
at runtime under the account's own terms.

Both are optional. With ``FMD_VOICE_ENABLED=false``, with no AWS credentials,
with an IAM policy that does not allow them, or past their daily ceiling, the
session says so in its ``voice`` state and the browser falls back to its own
Web Speech API where it has one.
"""

from app.voice.aws import VoiceUnavailable
from app.voice.listen import Ears, Listening
from app.voice.speech import Speaker

__all__ = ["Ears", "Listening", "Speaker", "VoiceUnavailable"]
