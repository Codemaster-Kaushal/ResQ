/* Hold-to-speak input.
 *
 * Uses the browser's own SpeechRecognition, which supports every language the
 * app offers. Two honest limitations, surfaced rather than hidden:
 *
 * - Chrome's implementation sends audio to a Google service, so it does not
 *   work offline. Typing and the signal chips still do.
 * - Firefox has no implementation at all.
 *
 * When it is unavailable the button is hidden rather than shown broken. The
 * backend accepts no audio, so what is transcribed becomes report text — the
 * citizen can edit it before sending.
 */

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;

export const voice = {
  get supported() {
    return Boolean(Recognition) && navigator.onLine;
  },

  recogniser: null,

  /**
   * Start listening. `onResult` receives interim and final transcripts;
   * `onEnd` fires once, whether it stopped cleanly or errored.
   */
  start(languageCode, { onResult, onEnd, onError }) {
    if (!Recognition) {
      onError?.('Speech input is not available in this browser');
      return false;
    }

    const recogniser = new Recognition();
    recogniser.lang = languageCode;
    recogniser.interimResults = true;
    recogniser.continuous = true;
    recogniser.maxAlternatives = 1;

    let transcript = '';

    recogniser.onresult = (event) => {
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) transcript += chunk;
        else interim += chunk;
      }
      onResult?.((transcript + interim).trim(), Boolean(transcript));
    };

    recogniser.onerror = (event) => {
      const reason = {
        'no-speech': 'No speech detected — try again',
        'audio-capture': 'No microphone available',
        'not-allowed': 'Microphone permission denied',
        network: 'Speech recognition needs a connection',
      }[event.error] || 'Could not capture speech';
      onError?.(reason);
    };

    recogniser.onend = () => {
      this.recogniser = null;
      onEnd?.(transcript.trim());
    };

    this.recogniser = recogniser;
    try {
      recogniser.start();
      return true;
    } catch {
      onError?.('Could not start the microphone');
      return false;
    }
  },

  stop() {
    try {
      this.recogniser?.stop();
    } catch { /* already stopped */ }
  },
};
