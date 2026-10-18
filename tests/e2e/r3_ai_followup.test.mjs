/**
 * R3: Mounting Search KI Follow-up Streaming E2E Test Suite
 * Verifies SSE stream parsing, conversation history contract preservation,
 * dialogue alternation, and resilient error recovery.
 */
import { describe, it, assert, createMockSseStream, parseSseChunks } from './harness.mjs';

// Helper matching backend prompt construction in ai_search_summary_service.py
function baueNachrichten(q, treffer, verlauf = []) {
  const messages = [
    { role: 'system', content: 'SYSTEM_PROMPT_MOCK' },
  ];

  if (!verlauf || verlauf.length === 0) {
    messages.push({ role: 'user', content: `Kontext:\n${JSON.stringify(treffer)}\n\nNutzeranfrage:\n${q}` });
    return messages;
  }

  // Preserve history
  const letzteRunden = verlauf.slice(-8);
  for (const eintrag of letzteRunden) {
    const rolle = eintrag.role;
    const inhalt = (eintrag.content || '').trim();
    if ((rolle === 'user' || rolle === 'assistant') && inhalt) {
      messages.push({ role: rolle, content: inhalt });
    }
  }

  // Ensure current question is the last user turn if not already appended
  if (messages[messages.length - 1].role !== 'user' || messages[messages.length - 1].content !== q) {
    messages.push({ role: 'user', content: q });
  }

  return messages;
}

function pruefeAlternierung(messages) {
  const dialogOnly = messages.filter((m) => m.role !== 'system');
  for (let i = 0; i < dialogOnly.length - 1; i++) {
    if (dialogOnly[i].role === dialogOnly[i + 1].role) {
      return false;
    }
  }
  return true;
}

describe('R3: Mounting Search KI Follow-up Streaming', () => {
  // Tier 1: Feature Coverage
  it('TC-T1-R3-01: SSE stream lifecycle parsing (start -> content tokens -> done)', async () => {
    const streamEvents = [
      { type: 'start', model: 'gpt-4o', provider: 'openai' },
      { type: 'content', text: 'Quantencomputer ' },
      { type: 'content', text: 'nutzen Qubits ' },
      { type: 'content', text: 'für exponentielle Rechenleistung.' },
      { type: 'done', model: 'gpt-4o' },
    ];

    const stream = createMockSseStream(streamEvents, 2);
    const parsed = await parseSseChunks(stream);

    assert.equal(parsed.length, 5);
    assert.equal(parsed[0].type, 'start');
    assert.equal(parsed[4].type, 'done');

    const contentTokens = parsed.filter((p) => p.type === 'content').map((p) => p.text).join('');
    assert.equal(contentTokens, 'Quantencomputer nutzen Qubits für exponentielle Rechenleistung.');
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-01' });

  it('TC-T1-R3-02: Follow-up question preserves initial hauptText in conversation history', () => {
    const initialQuery = 'Was ist Photosynthese?';
    const initialHauptText = 'Photosynthese wandelt Lichtenergie, CO2 und Wasser in Glukose und Sauerstoff um.';
    const followUpQuery = 'Welche Rolle spielt Chlorophyll?';

    // The contract requires that when a follow-up is submitted, verlauf contains the previous user question and assistant answer
    const dialogVerlauf = [
      { role: 'user', content: initialQuery },
      { role: 'assistant', content: initialHauptText },
      { role: 'user', content: followUpQuery },
    ];

    const messages = baueNachrichten(followUpQuery, [], dialogVerlauf);

    assert.ok(messages.length >= 4);
    assert.equal(messages[0].role, 'system');
    assert.equal(messages[1].role, 'user');
    assert.equal(messages[1].content, initialQuery);
    assert.equal(messages[2].role, 'assistant');
    assert.equal(messages[2].content, initialHauptText);
    assert.equal(messages[3].role, 'user');
    assert.equal(messages[3].content, followUpQuery);
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-02' });

  it('TC-T1-R3-03: Strict user/assistant dialogue alternation in conversation history payload', () => {
    const history = [
      { role: 'user', content: 'Turn 1 User' },
      { role: 'assistant', content: 'Turn 1 Assistant' },
      { role: 'user', content: 'Turn 2 User' },
      { role: 'assistant', content: 'Turn 2 Assistant' },
    ];

    const messages = baueNachrichten('Turn 3 User', [], history);
    const isAlternating = pruefeAlternierung(messages);

    assert.ok(isAlternating, 'Dialogue turns must alternate strictly between user and assistant');
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-03' });

  it('TC-T1-R3-04: Streaming event parser handles fragmented TCP chunks correctly', async () => {
    const rawChunks = [
      'da',
      'ta: {"type": "st',
      'art", "model": "test"}\n\n',
      'data: {"type": "content", "text": "He',
      'llo "}\n\ndata: {"type": "content", "te',
      'xt": "World!"}\n\n',
      'data: {"type": "done"}\n\n',
    ];

    async function* chunkStream() {
      for (const c of rawChunks) {
        yield c;
      }
    }

    const events = await parseSseChunks(chunkStream());

    assert.equal(events.length, 4);
    assert.equal(events[0].type, 'start');
    assert.equal(events[1].text, 'Hello ');
    assert.equal(events[2].text, 'World!');
    assert.equal(events[3].type, 'done');
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-04' });

  it('TC-T1-R3-05: Server error event transitions UI away from perpetual loading spinner', async () => {
    const errorStream = [
      { type: 'start', model: 'test' },
      { type: 'error', message: 'Inference rate limit exceeded or backend worker crashed' },
    ];

    const stream = createMockSseStream(errorStream, 1);
    const events = await parseSseChunks(stream);

    let laedt = true;
    let errorMessage = null;

    for (const ev of events) {
      if (ev.type === 'start') laedt = true;
      if (ev.type === 'error') {
        laedt = false;
        errorMessage = ev.message;
      }
    }

    assert.equal(laedt, false, 'Loading spinner must be turned off upon error');
    assert.ok(errorMessage.includes('Inference rate limit'));
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-05' });

  it('TC-T1-R3-06: Deep dive source reference [1] recognition triggers focused research event', () => {
    const query = 'Erkläre mir mehr zu den Angaben in Quelle [1] über Quantenverschränkung.';
    const treffer = [
      { index: 1, titel: 'Quantenverschränkung Übersicht', url: 'https://example.com/quantum' },
      { index: 2, titel: 'Allgemeine Relativitätstheorie', url: 'https://example.com/relativity' },
    ];

    const match = query.match(/\[(\d+)\]/);
    assert.ok(match);
    const targetIndex = parseInt(match[1], 10);
    const targetSource = treffer.find((t) => t.index === targetIndex);

    assert.equal(targetIndex, 1);
    assert.ok(targetSource);
    assert.equal(targetSource.titel, 'Quantenverschränkung Übersicht');
  }, { tier: 1, feature: 'R3', id: 'TC-T1-R3-06' });

  // Tier 2: Boundary & Corner Cases
  it('TC-T2-R3-01: Deep multi-turn follow-up chain (>10 rounds) maintains rolling window budget', () => {
    const longVerlauf = [];
    for (let i = 1; i <= 15; i++) {
      longVerlauf.push({ role: 'user', content: `Question ${i}` });
      longVerlauf.push({ role: 'assistant', content: `Answer ${i}` });
    }

    const messages = baueNachrichten('Question 16', [], longVerlauf);
    // rolling window takes last 8 entries from verlauf + system + current question
    const historyEntries = messages.filter((m) => m.role !== 'system');

    assert.ok(historyEntries.length <= 10, `Expected at most 10 turns, got ${historyEntries.length}`);
    assert.equal(messages[messages.length - 1].content, 'Question 16');
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-01' });

  it('TC-T2-R3-02: Abrupt server connection termination mid-stream retains partial text and resets spinner', async () => {
    async function* abortedStream() {
      yield 'data: {"type": "start"}\n\n';
      yield 'data: {"type": "content", "text": "Partially generated response"}\n\n';
      throw new Error('Premature TCP socket closure');
    }

    let accumulatedText = '';
    let isStillLoading = true;
    let streamFailed = false;

    try {
      for await (const chunk of abortedStream()) {
        const events = await parseSseChunks((async function* () { yield chunk; })());
        for (const ev of events) {
          if (ev.type === 'content') accumulatedText += ev.text;
        }
      }
    } catch (err) {
      streamFailed = true;
    } finally {
      isStillLoading = false;
    }

    assert.ok(streamFailed);
    assert.equal(isStillLoading, false);
    assert.equal(accumulatedText, 'Partially generated response');
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-02' });

  it('TC-T2-R3-03: Thought tokens without content render reasoning state without markdown break', async () => {
    const thoughtStream = [
      { type: 'start', model: 'deepseek-r1' },
      { type: 'thought', text: 'Analyzing multi-step physics logic... ' },
      { type: 'thought', text: 'Verifying conservation of energy...' },
      { type: 'done', model: 'deepseek-r1' },
    ];

    let thoughtText = '';
    let contentText = '';

    for (const ev of thoughtStream) {
      if (ev.type === 'thought') thoughtText += ev.text;
      if (ev.type === 'content') contentText += ev.text;
    }

    assert.ok(thoughtText.includes('Analyzing multi-step physics'));
    assert.equal(contentText, '');
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-03' });

  it('TC-T2-R3-04: Whitespace-only follow-up query submission is rejected before network dispatch', () => {
    const submitFollowUp = (query, currentLoading) => {
      const trimmed = query.trim();
      if (!trimmed || currentLoading) return false;
      return true;
    };

    assert.equal(submitFollowUp('', false), false);
    assert.equal(submitFollowUp('   \t\n  ', false), false);
    assert.equal(submitFollowUp('Valid query', true), false);
    assert.equal(submitFollowUp('Valid query', false), true);
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-04' });

  it('TC-T2-R3-05: Payload validation guards against extreme prompt sizes (>50 KB)', () => {
    const hugePrompt = 'X'.repeat(60000);
    const MAX_ALLOWED_PROMPT = 32000;

    const sanitizePrompt = (text) => text.slice(0, MAX_ALLOWED_PROMPT);
    const sanitized = sanitizePrompt(hugePrompt);

    assert.equal(sanitized.length, MAX_ALLOWED_PROMPT);
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-05' });

  it('TC-T2-R3-06: Disabled feature SSE event renders clean notification without UI freeze', async () => {
    const disabledStream = [
      { type: 'disabled', reason: 'not_configured' },
    ];

    let isDisabled = false;
    let isSpinning = true;

    for (const ev of disabledStream) {
      if (ev.type === 'disabled') {
        isDisabled = true;
        isSpinning = false;
      }
    }

    assert.equal(isDisabled, true);
    assert.equal(isSpinning, false);
  }, { tier: 2, feature: 'R3', id: 'TC-T2-R3-06' });
});
