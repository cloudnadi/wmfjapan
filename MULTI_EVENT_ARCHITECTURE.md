# Multi-Event Registration Architecture

## Overview

The system has been refactored to support **multiple events dynamically** without creating separate Lambda functions, DynamoDB tables, or API endpoints for each event type. This follows a **multi-tenant architecture** pattern, ensuring scalability, maintainability, and cost efficiency.

---

## Architecture Changes

### 1. **New Events Metadata Table**
- **Table Name**: `{project-name}-events-{suffix}`
- **Primary Key**: `eventType` (e.g., "housewarming_party", "cultural_event")
- **Purpose**: Stores event configuration and metadata
- **Global Secondary Indexes**:
  - `HostIdIndex`: Query events by host/tenant ID
  - `StatusIndex`: Query events by status (active, inactive, archived)

### 2. **Updated Registrations Table**
- **New Attribute**: `eventType` - Links registrations to specific events
- **New Global Secondary Index**: `EventTypeIndex` - Query registrations by event type

### 3. **Consolidated Lambda Functions**
- All Lambda functions now accept `eventType` as a parameter
- Event-specific configurations are fetched dynamically from the events table
- Removed hardcoded environment variables for event details (event_name, event_date, venue, etc.)

### 4. **Updated API Gateway Routes**
- **Old Route**: `/register` (single event)
- **New Route**: `/events/{eventType}/register` (dynamic, supports multiple events)
- Path parameter `eventType` is passed to Lambda for event-specific processing

---

## Data Model

### Events Table Schema

```json
{
  "eventType": "housewarming_party",  // Primary Key
  "eventName": "Housewarming Party",
  "eventDate": "July 12, 2026",
  "status": "active",  // active, inactive, archived
  "venue": "Edogawa East Citizen Hall",
  "description": "Join us for a joyful Onam & 40th Anniversary Celebration",
  "hostId": "host-123",  // For multi-tenant support
  "capacity": 100,
  "registeredCount": 45,
  "customFields": {
    "houseAddress": "3 Chome 3-8, Edogawa",
    "houseVisitTime": "3:30 PM - 5:30 PM",
    "dinnerVenue": "Citizen Hall",
    "dinnerTime": "6:00 PM - 8:00 PM"
  },
  "createdAt": "2026-01-01T00:00:00Z",
  "updatedAt": "2026-05-15T10:30:00Z",
  "expiresAt": 1735689600  // Optional: for TTL
}
```

### Registrations Table Schema

```json
{
  "registrationId": "uuid-123",  // Primary Key
  "eventType": "housewarming_party",  // Sort Key (for GSI)
  "email": "user@example.com",
  "name": "John Doe",
  "phone": "+81-90-1234-5678",
  "submittedAt": "2026-05-15T10:30:00Z",
  "additionalData": {
    "attendeeCount": 3,
    "specialRequests": "Vegetarian meal needed"
  }
}
```

---

## How to Add a New Event

### Step 1: Insert Event Metadata into the Events Table

Using AWS CLI:

```bash
aws dynamodb put-item \
  --table-name wmfjapan-events-15b470ae \
  --item '{
    "eventType": {"S": "cultural_event"},
    "eventName": {"S": "Japanese Cultural Evening"},
    "eventDate": {"S": "June 15, 2026"},
    "status": {"S": "active"},
    "venue": {"S": "Tokyo Cultural Center"},
    "description": {"S": "Experience Japanese culture through traditional arts and crafts"},
    "hostId": {"S": "host-123"},
    "capacity": {"N": "150"},
    "registeredCount": {"N": "0"},
    "customFields": {"M": {
      "time": {"S": "6:00 PM - 9:00 PM"},
      "dresscode": {"S": "Traditional attire encouraged"}
    }},
    "createdAt": {"S": "2026-05-15T00:00:00Z"},
    "updatedAt": {"S": "2026-05-15T00:00:00Z"}
  }' \
  --region ap-northeast-1
```

### Step 2: Use the API Endpoint

Register for the new event using the dynamic API:

```bash
curl -X POST \
  https://api.example.com/events/cultural_event/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Jane Doe",
    "email": "jane@example.com",
    "phone": "+81-90-9876-5432",
    "additionalData": {
      "foodPreference": "vegetarian",
      "attendeeCount": 2
    }
  }'
```

---

## Lambda Function Changes

### Register Handler

**Old Behavior:**
- Accepted `eventType` in request body
- Fetched event metadata from registrations table (incorrect)
- Had hardcoded event information in environment variables

**New Behavior:**
- Accepts `eventType` from URL path parameter (e.g., `/events/{eventType}/register`)
- Fetches event metadata from the dedicated events table
- Validates event status before accepting registrations
- Checks for duplicate registrations
- Sends event-specific confirmation and admin notification emails

### Other Lambda Functions

The following functions should be updated similarly:
- `get_registrations`: Filter registrations by `eventType` using the EventTypeIndex
- `export_csv`: Generate CSV exports by `eventType`
- `confirm_email`: Handle confirmation links with `eventType` context

---

## IAM Permissions

Lambda functions now require access to **both** tables:

- **Registrations Table**: `PutItem`, `GetItem`, `UpdateItem`, `Scan`, `Query`
- **Events Table**: `GetItem`, `Query`, `Scan` (read-only)

---

## Benefits of This Architecture

| Benefit | Description |
|---------|-------------|
| **Scalability** | Add unlimited events without code changes or resource duplication |
| **Cost Efficiency** | Single set of Lambda functions and API endpoints serve all events |
| **Maintainability** | Centralized event configuration, easier to update and debug |
| **Flexibility** | Support diverse event types with different requirements |
| **Multi-Tenancy** | Easily support different hosts/organizations with `hostId` |
| **Data Consistency** | All registrations stored in one table for easier analytics |
| **No Redeployment** | Add new events without redeploying infrastructure |

---

## Future Enhancements

1. **Event Management Dashboard**: Admin UI to create/update/delete events
2. **Event Validation Service**: Custom validation rules per event type
3. **EventBridge Integration**: Publish registration events for downstream processing
4. **Analytics**: Aggregated registration analytics across all events
5. **Batch Operations**: Import bulk events or registrations
6. **Template System**: Reuse event configurations with templates
7. **Custom Notification Workflows**: Define notification rules per event

---

## Migration from Single-Event System

If migrating from the old single-event system:

1. Create an entry in the events table for the existing event
2. Migrate existing registrations to the registrations table (add `eventType` field)
3. Update frontend API calls to use the new endpoint format: `/events/{eventType}/register`
4. Test thoroughly before deploying to production

---

## Troubleshooting

### Event Not Found (404)
- **Cause**: Event metadata not inserted in the events table
- **Solution**: Insert the event using the `put-item` command above

### "Event is not currently accepting registrations"
- **Cause**: Event status is not "active"
- **Solution**: Update the event status in the events table to "active"

### Registration Fails
- **Cause**: Missing or invalid request fields
- **Solution**: Ensure `name`, `email`, `phone` fields are provided and valid

---

## API Examples

### Create a Housewarming Party Event

```bash
aws dynamodb put-item \
  --table-name wmfjapan-events-{suffix} \
  --item '{
    "eventType": {"S": "housewarming_party"},
    "eventName": {"S": "New Home Celebration"},
    "eventDate": {"S": "July 20, 2026"},
    "status": {"S": "active"},
    "venue": {"S": "123 Oak Street"},
    "description": {"S": "Join us for a joyful Onam & 40th Anniversary Celebration!"},
    "hostId": {"S": "family-123"},
    "capacity": {"N": "50"},
    "registeredCount": {"N": "0"},
    "createdAt": {"S": "2026-05-15T00:00:00Z"},
    "updatedAt": {"S": "2026-05-15T00:00:00Z"}
  }' \
  --region ap-northeast-1
```

### Register for an Event

```bash
curl -X POST \
  https://your-api-gateway-url/events/housewarming_party/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Alice Johnson",
    "email": "alice@example.com",
    "phone": "+81-90-1111-2222",
    "additionalData": {
      "attendeeCount": 2,
      "dietaryRestrictions": "no nuts"
    }
  }'
```

### Query Registrations for a Specific Event

```bash
aws dynamodb query \
  --table-name wmfjapan-registrations-{suffix} \
  --index-name EventTypeIndex \
  --key-condition-expression "eventType = :et" \
  --expression-attribute-values '{":et":{"S":"housewarming_party"}}' \
  --region ap-northeast-1
```

---

## File Changes Summary

1. ✅ **events_table.tf**: New DynamoDB table for event metadata
2. ✅ **dynamodb.tf**: Updated registrations table with `eventType` attribute and GSI
3. ✅ **lambda.tf**: Updated IAM policy to include events table access
4. ✅ **api_gateway.tf**: New dynamic route `/events/{eventType}/register`
5. ✅ **lambdas/register/handler.py**: Refactored to fetch event metadata from events table

---

## Next Steps

1. Deploy the updated infrastructure using Terraform
2. Insert test events into the events table
3. Update frontend to use the new API endpoint
4. Test registration flows for multiple events
5. Monitor CloudWatch logs for any errors

