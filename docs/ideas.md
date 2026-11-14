# Planned extensions

## Parallel events

**Today:** there is always exactly one active event. Creating a new one archives the previous one,
and all newly fetched mails go to the new event. If two events lie in the future (e.g. next month
and the month after), registrations for the first one end up in the wrong list.

**Proposal:**

- **Several active events.** An event is archived only when it is closed or automatically after its
  date.
- **Assignment while fetching**, in this order:
  1. Reply to the event's newsletter (In-Reply-To/References): unambiguous.
  2. Title or date in the subject or body.
  3. Otherwise the nearest event, marked "please check".
- **Inbox:** all new mails together. The card shows the suggested event, which can be changed with
  one click. For "I'll come to both" a person can be registered for both.
- **Overview and registration list:** an event selector at the top. Confirmation mail and ICS file
  use the data of the respective event.
- **One newsletter for several dates:** it can be picked for several events. Title and date in the
  reply then decide; if that is ambiguous, the app asks.

**Affected:** `services/fetching.py` and `classifier.py` (assignment), `services/events.py`
(several active), `ui/inbox.py`, `ui/overview.py`, `ui/registration_list.py`, `ui/events.py`
(no automatic archiving). The database needs no migration: `mail.event_id` and
`registration.event_id` already exist.
