# Project: MSM E2EE Messenger Hardening & Interoperability

## Architecture
- **Frontend Architecture**: React + TypeScript in `frontend/src/`
  - `pages/Messenger.tsx`: Core direct chat timeline, envelope processing, optimistic messaging, receipt evaluation, route sync.
  - `services/e2eeCrypto.ts`: Hybrid envelope (`sv-e2ee-hybrid-v1:`) and direct channel key (`deriveDirectChannelKey`), RSA-OAEP + AES-GCM cryptography.
  - `api/social.ts`: Backend social endpoints, public key management (`/social/e2ee/keys`), media download with Bearer auth.
  - `components/social/ChatMediaAttachments.tsx`: Image/file attachment rendering and blob decryption.
  - `components/notifications/ServerIncidentNotifier.tsx`: SSE real-time event pipeline for incoming messages and delivery receipts.
  - `components/social/E2EEChatModal.tsx`: Key registration and direct modal chat.
  - `services/deliveryReceiptService.ts`: Blind mailbox delivery receipt envelope creation and transmission.
- **Backend Architecture**: FastAPI + SQLAlchemy in `backend/`
  - `models/e2ee_blind_envelope.py`: Blind mailbox envelope database model (`e2ee_blind_envelopes`).
  - `routers/social.py`: Blind mailbox relay and retrieval endpoints (`/api/social/e2ee/relay`, `/api/social/e2ee/mailbox/{id}`).
  - `services/social_service.py`: Relay and mailbox storage service.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Deterministic Reproduction Tests | Automated vitest test reproducing decryption failure, stuck receipts, reload history drop, and media failures | M0 | R1, survey |
| 2 | E2EE Key Synchronization & Hybrid Decryption | Synchronize/update public key on mismatch; fallback channel key on failure; eliminate "Verschlüsselte Nachricht" | M1 | R1, survey |
| 3 | Control Packet Silencing | Ensure delivery/read receipt control envelopes are never rendered as chat messages, even if unparsed/failed | M1 | R1, R3, survey |
| 4 | Reload Hydration & History Persistence | URL/route contact persistence (`?userId=...`), reload mailbox hydration, persistent message restore | M1 | R2, survey |
| 5 | Delivery & Read Receipt Tick Synchronization | Sync optimistic message ID with server envelope ID; propagate delivery receipts across SSE / mailbox | M1 | R3, survey |
| 6 | Cross-Client Media Exchange & Tauri Auth | Use authenticated api() in `downloadChatMedia`; fix cryptoContext userBId for own messages on reload | M1 | R1, survey |
| 7 | Full E2E & Regressions Test Validation | Pass all social/messenger test suites and `npm run build` with 0 errors | M2 | Acceptance criteria |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M0 | Deterministic Reproduction | Automated test reproducing failures prior to fix (`MessengerReproduction.test.tsx`) | none | DONE |
| M1 | Core E2EE Messenger Repair | Implement key sync, reload hydration, receipt sync, and media exchange fixes | M0 | IN_PROGRESS |
| M2 | Test Validation, Hardening & Audit | E2E & unit suites, Reviewers, Challengers, Forensic Auditor, clean build | M1 | PLANNED |

## Code Layout & File Ownership for M1
- `frontend/src/pages/Messenger.tsx`: Main chat page, key sync, message loop, receipt evaluation, optimistic message ID mapping, route sync
- `frontend/src/services/e2eeCrypto.ts`: Hybrid envelope and direct channel crypto functions, key generation and storage
- `frontend/src/api/social.ts`: Social and media API functions including `downloadChatMedia`
- `frontend/src/components/social/ChatMediaAttachments.tsx`: Media attachment rendering and crypto context
- `frontend/src/components/notifications/ServerIncidentNotifier.tsx`: SSE notification handler for message and receipt events
- `frontend/src/components/social/E2EEChatModal.tsx`: Key registration check in chat modal
- `frontend/src/services/deliveryReceiptService.ts`: Delivery receipt creation and dispatch
- `frontend/src/pages/MessengerReproduction.test.tsx`: Companion tests validating fixes
