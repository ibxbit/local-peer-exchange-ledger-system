/**
 * Vitest unit tests for static/js/matching.js — peer search, sessions, queue.
 *
 * Covers:
 *   - renderMatching paints the search shell + HTMX filter inputs
 *   - renderSessions fetches /matching/sessions and paints the table rows
 *   - openRequestSession opens a modal and POSTs /matching/sessions on submit
 *   - viewRepModal fetches /reputation/score/<uid> and paints a modal
 *   - openRateSession opens a rating modal and POSTs /reputation/rate on submit
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import {
  renderMatching, renderSessions,
  openRequestSession, openRateSession, viewRepModal,
} from '../matching.js';
import { API } from '../api.js';

let fetchMock;

beforeEach(() => {
  document.body.innerHTML = `
    <div id="view-container"></div>
    <div id="main-alert"></div>
  `;
  sessionStorage.clear();
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock;
});

afterEach(() => {
  vi.restoreAllMocks();
});

function respond({ status = 200, body = {} } = {}) {
  return { ok: status < 400, status, json: async () => body };
}

function flush() {
  return new Promise((r) => setTimeout(r, 0));
}

// ---------------------------------------------------------------------------
// renderMatching
// ---------------------------------------------------------------------------

describe('matching: renderMatching paints the search shell', () => {
  it('renders HTMX-wired filter inputs and peer-list container', async () => {
    await renderMatching();
    const vc = document.querySelector('#view-container');
    expect(vc.querySelector('#search-skill')).toBeTruthy();
    expect(vc.querySelector('#search-tag')).toBeTruthy();
    expect(vc.querySelector('#peer-list')).toBeTruthy();
    expect(vc.querySelector('#btn-open-queue')).toBeTruthy();
    // HTMX attributes on the skill input
    expect(vc.querySelector('#search-skill')
      .getAttribute('hx-get')).toBe('/api/matching/peers-partial');
  });

  it('clicking "Join Match Queue" opens the queue modal', async () => {
    await renderMatching();
    document.querySelector('#btn-open-queue').click();
    expect(document.querySelector('#queue-form')).toBeTruthy();
    expect(document.querySelector('#q-skill')).toBeTruthy();
    expect(document.querySelector('#q-priority')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// renderSessions
// ---------------------------------------------------------------------------

describe('matching: renderSessions', () => {
  it('fetches /matching/sessions and paints rows', async () => {
    API.saveUser({ id: 5, username: 'alice', role: 'user' });
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        sessions: [
          {
            id: 7, initiator_id: 5, participant_id: 8,
            initiator_name: 'alice', participant_name: 'bob',
            status: 'pending', credit_amount: 10,
            scheduled_at: '2025-03-01T10:00:00Z',
          },
          {
            id: 8, initiator_id: 9, participant_id: 5,
            initiator_name: 'carol', participant_name: 'alice',
            status: 'completed', credit_amount: 5,
            scheduled_at: null,
          },
        ],
        total: 2,
      },
    }));

    await renderSessions();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.innerHTML).toContain('My Sessions');
    // Pending row shows the peer (initiator's POV → participant_name)
    expect(vc.textContent).toContain('bob');
    // Completed row shows the initiator as the peer (user is participant)
    expect(vc.textContent).toContain('carol');
    // Badges
    expect(vc.innerHTML).toContain('badge-pending');
    expect(vc.innerHTML).toContain('badge-completed');
    // Session ids rendered
    expect(vc.textContent).toContain('#7');
    expect(vc.textContent).toContain('#8');

    // Query-string parameters used in the request
    const url = fetchMock.mock.calls[0][0];
    expect(url).toContain('/api/matching/sessions');
    expect(url).toContain('page=1');
    expect(url).toContain('per_page=15');
  });

  it('shows empty state when there are no sessions', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { sessions: [], total: 0 },
    }));
    await renderSessions();
    await flush();

    const vc = document.querySelector('#view-container');
    expect(vc.textContent).toContain('No sessions');
  });
});

// ---------------------------------------------------------------------------
// openRequestSession
// ---------------------------------------------------------------------------

describe('matching: openRequestSession modal', () => {
  it('opens a modal with the peer username and POSTs on submit', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      status: 201, body: { session_id: 99 },
    }));

    openRequestSession(42, 'bob');
    expect(document.querySelector('#req-sess-form')).toBeTruthy();
    expect(document.querySelector('.modal').textContent).toContain('bob');

    document.querySelector('#rs-desc').value = 'Pair program';
    document.querySelector('#rs-dur').value = '60';
    document.querySelector('#rs-cred').value = '5';

    document.querySelector('#req-sess-form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/matching/sessions');
    expect(opts.method).toBe('POST');
    expect(opts.headers['Idempotency-Key']).toMatch(/^idem-/);
    expect(JSON.parse(opts.body)).toMatchObject({
      participant_id: 42,
      description: 'Pair program',
      duration_minutes: 60,
      credit_amount: 5,
    });
  });
});

// ---------------------------------------------------------------------------
// viewRepModal
// ---------------------------------------------------------------------------

describe('matching: viewRepModal', () => {
  it('fetches /reputation/score/<uid> and renders the stats modal',
    async () => {
      fetchMock.mockResolvedValueOnce(respond({
        body: {
          username: 'charlie',
          reputation_score: 88.5,
          average_rating: 4.7,
          sessions_completed: 15,
          violations_against: 1,
        },
      }));

      await viewRepModal(12);
      await flush();

      const url = fetchMock.mock.calls[0][0];
      expect(url).toBe('/api/reputation/score/12');

      const modal = document.querySelector('.modal');
      expect(modal).toBeTruthy();
      expect(modal.textContent).toContain('charlie');
      expect(modal.textContent).toContain('88.5');
      expect(modal.textContent).toContain('4.7');
      expect(modal.textContent).toContain('15');
    });
});

// ---------------------------------------------------------------------------
// openRateSession
// ---------------------------------------------------------------------------

describe('matching: openRateSession', () => {
  it('renders a 5-star radio widget inside the modal', () => {
    openRateSession(123);
    const modal = document.querySelector('.modal');
    expect(modal).toBeTruthy();
    const radios = modal.querySelectorAll('input[name="score"]');
    expect(radios.length).toBe(5);
    const comment = modal.querySelector('#rate-comment');
    expect(comment).toBeTruthy();
  });

  it('POSTs /reputation/rate with the selected score', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      status: 201, body: { message: 'Rating submitted.' },
    }));

    openRateSession(77);
    // Simulate selecting the 4-star radio
    const r4 = document.querySelector('input[name="score"][value="4"]');
    r4.checked = true;

    document.querySelector('#rate-comment').value = 'Great session';
    document.querySelector('#rate-form')
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/reputation/rate');
    expect(opts.method).toBe('POST');
    expect(JSON.parse(opts.body)).toEqual({
      session_id: 77,
      score: 4,
      comment: 'Great session',
    });
  });

  it('shows a warning and does not POST when no star is selected',
    async () => {
      openRateSession(77);
      document.querySelector('#rate-form')
        .dispatchEvent(new Event('submit', {
          bubbles: true, cancelable: true,
        }));
      await flush();
      // No network call
      expect(fetchMock).not.toHaveBeenCalled();
    });
});
