# Event admin guide

For organisers. No developer needed for any of this. Sign in at **/admin** with your admin account, then open the **Event platform** tab.

Who can do what: **Admins** can do everything (including delete events and erase personal data). **EventManagers** can create and run events, take registrations and check people in, but cannot delete events or erase personal data.

## 1. Create an event
1. **Event platform → New event.** Fill in title, URL slug (the link, e.g. `/events/summer-picnic`), description, category.
2. **Schedule:** start/end in Japan time, when registration opens and closes, total capacity, and whether to keep a waitlist.
3. **Location:** venue and address. The online link is shown only to confirmed attendees.
4. **Access:** members only (valid now *and* covering the renewal year), and whether guests may register (on by default).
5. **Emails:** edit the confirmation and reminder text. Placeholders: `{name} {event_title} {ticket_type} {registration_id} {event_date} {venue} {event_url}`.
6. Save, then add **Design** (cover image, colour), **Tickets & pricing** and the **Registration form**.

## 2. Tickets and pricing
Each ticket type has a member price, a non-member price (whole yen, `0` = free), an optional early-bird price for NPO members with a cut-off date, an optional quantity limit and a per-person cap. Prices are always calculated on the server.

## 3. Registration form
The **Form** tab lets you add, reorder and relabel fields (text, choice, checkbox, date, file upload, repeating groups…) and show a field only when another answer matches. Name, email, phone, membership info and the two consent boxes are locked: you can relabel them but not remove them. Editing a form creates a new version; people who already registered keep the version they used. Use **Preview** to try it as a guest or as a member before publishing.

## 4. Publish
Set the status to **Published** (needs a start time and at least one ticket on sale). **Unlisted** works by link only; **Closed** stops new registrations; **Archived** hides the event. Duplicate an event to reuse its tickets and form.

## 5. Manage registrations
Open the event → **Registrations**.
- **Dashboard:** counts, revenue, capacity used, waitlist, check-ins.
- **Table:** search, filter, sort; click a row for details. Edit answers, add a note, add a registration by hand (phone/walk-in).
- **Payments:** card payments confirm automatically. For bank/PayPay transfers press **Mark paid** once the money arrives. Unpaid holds are released after 30 minutes (card) or 7 days (bank/PayPay).
- **Waitlist:** press **Promote** to move someone in when a place opens.
- **Cancel / refund:** cancelling a paid registration puts it in *refund pending*. Refund via Square from the detail panel, or record a manual refund. If a *payment amount mismatch* warning shows, check the payment before confirming.
- **Export:** CSV or Excel, filtered the same as the table.
- **Email registrants:** pick an audience, use placeholders, send a test to yourself first.

## 6. On the day: check-in
Open **Check-in** on a phone, allow the camera, and scan the QR code on each attendee’s confirmation. Green = in; amber = already checked in or payment due; red = not found/cancelled. You can also check people in from the table, and undo a mistake.

## 7. Personal data requests (Admins only)
On a cancelled registration (or after the event ended), **Erase personal data** removes the name, contact details, answers and uploaded files. Totals and payment facts remain for accounting. It cannot be undone. Registrations awaiting a refund cannot be erased until the refund is recorded.

## Troubleshooting
| Symptom | What to do |
|---|---|
| “Someone else changed this” | Reload, then redo your edit. |
| Can’t publish | Add a start time and a ticket that is on sale. |
| Person says they paid by bank but is still pending | Check the account, then **Mark paid**. |
| Event shows “sold out” but seats are held | Unpaid holds count until they expire; cancel the hold to free the seat. |
| Anything else | Contact the site maintainer with the registration ID (`NK-XXXX-XXXX`). |
