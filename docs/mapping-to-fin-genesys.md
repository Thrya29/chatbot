# Mapping to Intercom Fin and Genesys Cloud

The mocks are shaped after public vendor documentation as read in late 2026. Endpoint names, limits and behaviour
change, so **verify each item against current docs before building on it.** Nothing here comes from inside any
company's configuration.

## AI assistant side (Fin-style)

| Lab | Real-world equivalent |
|---|---|
| `detection.py` + `router.py` | Escalation rules and guidance on the AI agent, or a procedure step that decides to hand over |
| `withdrawal.py` | A procedure that calls a data connector (funding transaction status) and a code step for the date logic |
| `HELP_CONTENT` | Retrieval over approved help-centre content |
| `MockAssistantPlatform.escalate` → signed `conversation.escalated` | The platform's webhook for a conversation being assigned to the human team |
| `reply_as_admin(..., idempotency_key)` | Posting a reply into the same conversation through the platform's REST API as a team member |
| `close_after_human` + `csat_requested` | Closing the conversation so the platform's CSAT survey fires |

## Contact-centre side (Genesys Cloud)

| Lab | Genesys Cloud |
|---|---|
| `POST /api/v2/conversations/messages/{integrationId}/inbound/open/message` | Open Messaging inbound message API |
| Deprecated path rejected | The older `/api/v2/conversations/messages/inbound/open` endpoint is documented as deprecated, with retirement planned for **16 Nov 2026**. Check the current status |
| `channel.metadata.customAttributes` → participant data | Custom attributes on an Open Messaging message become participant data that an Architect flow can read. In the mock, a top-level `customAttributes` gets a 400 |
| `X-Hub-Signature-256: sha256=<hex>` on outbound | Open Messaging outbound webhook signature, verified with the integration's secret |
| `channel.from.id` with `idType: Opaque` | Stable external id per client conversation, so follow-up messages join the same conversation |
| `_architect_inbound_flow` | Inbound message flow: Get Participant Data → decision on `hl_queue` / `hl_priority` → Transfer to ACD with priority; fallback string match on the header line; default queue |
| ACD in `_acd` / `tick` | Queue routing: priority then time in queue, skills, language skill, bullseye ring widening after a timeout, agent utilisation capacity (3 messages) |
| `estimated_wait` returns None when priority reorders the queue | Estimated wait is unreliable when priority jumps the queue, so the client is told plainly instead of given a number |
| `wrap_up(..., complaint=None)` blocked when `possible_complaint` | Wrap-up codes plus a required field / script question, so possible complaints get a yes or no from the agent |
| `conversation_ended` event | Disconnect event on the outbound webhook, used to close the chat thread |

## Queues (from the case brief, mapped to names)

FundingPayments · TradingSupport · AccountKYC · PlatformTechnical · Complaints · PriorityVulnerable · Security.
`Security` is an addition. The brief's queues don't include one, and an unrecognised withdrawal at 02:00 on a
Saturday shouldn't wait in Funding.

## What would differ in a real deployment

- A native vendor-to-vendor integration may exist or be preferable. The bridge pattern here is the fallback that
  works with any two systems that have webhooks and a REST API, and it keeps the contract under your control.
- Attribute size limits, allowed characters and the number of custom attributes are set by the vendor. The contract
  keeps every attribute short and flat for that reason.
