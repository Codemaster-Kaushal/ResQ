/* Language-independent situation signals.
 *
 * The backend's local scorer is English keyword-based (TRD §4.1): it looks for
 * "trapped", "bleeding", "children", "rising water" and so on. A report written
 * in Kannada therefore classifies as `other` and scores 10 — the lowest band —
 * however grave it actually is. That is a real limitation, not a hypothetical.
 *
 * These chips close the gap without touching the backend. The citizen taps what
 * is happening in their own language; the app appends a normalised English
 * summary to the report. The original wording is always kept and sent first,
 * exactly as written.
 *
 * The phrasing below is chosen to match the scorer's actual patterns, so a
 * tapped chip reliably produces the reason code it should. When the AI layer
 * lands, Gemini reads all six languages natively and these become a
 * convenience rather than a necessity.
 */

export const SIGNALS = [
  { key: 'trapped', i18n: 'signal.trapped', phrase: 'people are trapped under the debris' },
  { key: 'collapse', i18n: 'signal.collapse', phrase: 'a building has collapsed' },
  { key: 'fire', i18n: 'signal.fire', phrase: 'there is a fire with heavy smoke' },
  { key: 'water', i18n: 'signal.water', phrase: 'water is rising fast' },
  { key: 'noExit', i18n: 'signal.noExit', phrase: 'there is no way out' },
  { key: 'injured', i18n: 'signal.injured', phrase: 'someone is injured' },
  { key: 'bleeding', i18n: 'signal.bleeding', phrase: 'someone is bleeding badly' },
  { key: 'unconscious', i18n: 'signal.unconscious', phrase: 'someone is unconscious' },
  { key: 'notBreathing', i18n: 'signal.notBreathing', phrase: 'someone is not breathing' },
  { key: 'children', i18n: 'signal.children', phrase: 'children are present' },
  { key: 'elderly', i18n: 'signal.elderly', phrase: 'an elderly person is present' },
  { key: 'disabled', i18n: 'signal.disabled', phrase: 'a disabled person cannot move' },
  { key: 'pregnant', i18n: 'signal.pregnant', phrase: 'a pregnant woman needs help' },
];

/** Compose the English sentence the scorer will read. */
export function signalSentence(keys) {
  const phrases = SIGNALS.filter((s) => keys.includes(s.key)).map((s) => s.phrase);
  if (!phrases.length) return '';
  const joined = phrases.length === 1
    ? phrases[0]
    : `${phrases.slice(0, -1).join(', ')} and ${phrases[phrases.length - 1]}`;
  return `Reported situation: ${joined}.`;
}

/** Count of people, worded so the scorer's head-count parser picks it up. */
export function peopleSentence(count) {
  if (!count || count < 1) return '';
  return count === 1
    ? 'One person needs help.'
    : `${count} people need help.`;
}

/**
 * Build the body actually sent to the backend.
 *
 * Order matters: the citizen's own words come first so a human reading the
 * report sees what was actually said, not a machine's paraphrase of it.
 */
export function composeBody({ text, signals = [], people = 0, profile = '', family = '', language }) {
  const parts = [text.trim()];

  if (language && language !== 'en') {
    parts.push(`[Reported in ${language}. Original wording preserved above.]`);
  }

  const situation = signalSentence(signals);
  if (situation) parts.push(situation);

  const heads = peopleSentence(people);
  if (heads) parts.push(heads);

  if (profile) parts.push(profile);
  if (family) parts.push(family);

  return parts.filter(Boolean).join('\n\n').slice(0, 5000);
}
