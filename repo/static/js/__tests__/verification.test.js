/**
 * Vitest unit tests for static/js/verification.js — identity verification panel.
 *
 * The module exports renderVerificationPanel(containerId). It fetches
 * /api/verification/status and renders one of four states:
 *   not_submitted / rejected — submit form
 *   pending                   — "Under Review"
 *   verified                  — "✅ Verified"
 *
 * Tests stub fetch and assert on the DOM + form-submit request shape.
 */

import {
  describe, it, expect, beforeEach, afterEach, vi,
} from 'vitest';

import { renderVerificationPanel } from '../verification.js';

let fetchMock;

beforeEach(() => {
  document.body.innerHTML = '<div id="ver-panel"></div>';
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
// Rendering
// ---------------------------------------------------------------------------

describe('verification: renderVerificationPanel states', () => {
  it('renders the submit form when status is "not_submitted"', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: { status: 'not_submitted' },
    }));

    await renderVerificationPanel('ver-panel');
    await flush();

    const panel = document.querySelector('#ver-panel');
    expect(panel.querySelector('#ver-form')).toBeTruthy();
    expect(panel.querySelector('#ver-doc-type')).toBeTruthy();
    // All four document types rendered as <option>
    const options = panel.querySelectorAll('#ver-doc-type option');
    const vals = Array.from(options).map((o) => o.value);
    expect(vals).toEqual(
      expect.arrayContaining(['passport', 'national_id',
        'drivers_license', 'utility_bill']));
    expect(panel.querySelector('#btn-ver-submit')).toBeTruthy();
  });

  it('renders "Under Review" for pending submissions', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        status: 'submitted',  // value other than "not_submitted"
        verification: {
          status: 'pending',
          document_type: '[masked]',
          submitted_at: '2025-03-01T00:00:00Z',
        },
      },
    }));

    await renderVerificationPanel('ver-panel');
    await flush();

    const panel = document.querySelector('#ver-panel');
    expect(panel.textContent).toContain('Under Review');
    expect(panel.innerHTML).toContain('badge-pending');
    // No submit form rendered in this state
    expect(panel.querySelector('#ver-form')).toBeNull();
  });

  it('renders "Verified" for verified submissions', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        status: 'submitted',
        verification: {
          status: 'verified',
          document_type: '[verified]',
          reviewed_at: '2025-03-02T12:00:00Z',
        },
      },
    }));

    await renderVerificationPanel('ver-panel');
    await flush();

    const panel = document.querySelector('#ver-panel');
    expect(panel.textContent).toContain('Verified');
    expect(panel.innerHTML).toContain('badge-verified');
  });

  it('re-renders a rejection banner with the admin notes', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      body: {
        status: 'submitted',
        verification: {
          status: 'rejected',
          notes: 'Blurry photo',
          document_type: '[rejected]',
        },
      },
    }));

    await renderVerificationPanel('ver-panel');
    await flush();

    const panel = document.querySelector('#ver-panel');
    expect(panel.textContent).toContain('rejected');
    expect(panel.textContent).toContain('Blurry photo');
    // Resubmit form is rendered so the user can re-upload
    expect(panel.querySelector('#ver-form')).toBeTruthy();
  });

  it('renders an error banner when the API call fails', async () => {
    fetchMock.mockResolvedValueOnce(respond({
      status: 500, body: { error: 'DB down' },
    }));
    await renderVerificationPanel('ver-panel');
    await flush();

    const panel = document.querySelector('#ver-panel');
    expect(panel.innerHTML).toContain('alert-error');
    expect(panel.textContent).toContain('DB down');
  });

  it('is a no-op when the target container does not exist', async () => {
    // No div with id "missing" exists
    await expect(renderVerificationPanel('missing')).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Submission
// ---------------------------------------------------------------------------

describe('verification: submit handler', () => {
  it('POSTs /verification/submit with the document fields',
    async () => {
      // First call: GET status → not_submitted
      // Second call: POST submit → 201
      // Third call: GET status again (re-render)
      fetchMock
        .mockResolvedValueOnce(respond({ body: { status: 'not_submitted' } }))
        .mockResolvedValueOnce(respond({
          status: 201, body: { verification_id: 5 },
        }))
        .mockResolvedValueOnce(respond({
          body: {
            status: 'submitted',
            verification: { status: 'pending',
                            document_type: '[masked]',
                            submitted_at: '2025-03-03T00:00:00Z' },
          },
        }));

      await renderVerificationPanel('ver-panel');
      await flush();

      document.querySelector('#ver-doc-type').value = 'national_id';
      document.querySelector('#ver-doc-data').value = 'ID-0001';

      document.querySelector('#ver-form').dispatchEvent(new Event('submit', {
        bubbles: true, cancelable: true,
      }));
      await flush();
      await flush();
      await flush();

      const submitCall = fetchMock.mock.calls.find(
        ([u]) => u === '/api/verification/submit');
      expect(submitCall).toBeDefined();
      const [, opts] = submitCall;
      expect(opts.method).toBe('POST');
      expect(JSON.parse(opts.body)).toEqual({
        document_type: 'national_id',
        document_data: 'ID-0001',
      });
    });
});
